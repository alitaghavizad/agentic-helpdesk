from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Clearance, Role

_KNOWN_ROLES = {r.value for r in Role}

# A tuple, not a set: list(...) order must be deterministic since it feeds
# a Chroma `$in` clause directly. "Overview" is the chunker's synthetic
# frontmatter chunk (app.rag.chunking.OVERVIEW_SECTION) — it's where
# "Primary specialization" actually lives in the source documents, since
# that field has no "##" heading of its own. Together these two sections
# satisfy spec section 6.2's "routing and specialization sections" grant
# for standard/sensitive employees.
RESTRICTED_HELPDESK_SECTIONS = ("Overview", "Routing guidance")

ACCESS_CLASSIFICATION_MAP = {
    "Standard": Clearance.STANDARD,
    "Sensitive business-data access": Clearance.SENSITIVE,
    "Privileged production access with approval": Clearance.PRIVILEGED,
}


class RetrievalDenied(PermissionError):
    """Raised when a principal has no retrieval access to a collection at all."""


@dataclass(frozen=True)
class Allow:
    pass


@dataclass(frozen=True)
class Deny:
    reason: str


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "guest"
    user_id: str | None
    role: str  # "guest" | "employee" | "helpdesk" | "admin"
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None
    guest_name: str | None = None
    guest_email: str | None = None


def map_access_classification(raw: str) -> Clearance:
    raw = raw.strip()
    if raw not in ACCESS_CLASSIFICATION_MAP:
        raise ValueError(f"Unknown access classification: {raw!r}")
    return ACCESS_CLASSIFICATION_MAP[raw]


def map_escalation_authority(raw: str) -> str:
    normalized = raw.strip().lower()
    if normalized not in {"standard", "high"}:
        raise ValueError(f"Unknown escalation authority: {raw!r}")
    return normalized


def retrieval_filter(
    principal: Principal,
    collection: str,
    *,
    helpdesk_visible_employee_ids: list[str] | None = None,
) -> dict:
    """
    Returns a Chroma `where` clause scoped to what `principal` may see in
    `collection`. Raises RetrievalDenied if the principal has no access to
    this collection at all. The filter returned here is computed
    server-side and must be merged with AND into every retrieval call — a
    model-supplied filter is never accepted (spec section 6.2).
    """
    if collection not in ("employees", "helpdesk", "lessons"):
        raise ValueError(f"unknown collection: {collection!r}")

    if principal.role not in _KNOWN_ROLES:
        raise RetrievalDenied(f"unrecognized role: {principal.role!r}")

    if principal.role == "guest":
        raise RetrievalDenied(f"guests cannot search {collection!r}")

    if collection == "lessons":
        return {}

    if principal.role == "admin":
        return {}

    if collection == "helpdesk":
        if principal.role == "helpdesk":
            return {}
        if principal.role == "employee" and principal.clearance == Clearance.PRIVILEGED.value:
            return {}
        return {"section": {"$in": list(RESTRICTED_HELPDESK_SECTIONS)}}

    # collection == "employees"
    if principal.role == "helpdesk":
        ids = helpdesk_visible_employee_ids or []
        return {"employee_id": {"$in": ids}}

    if principal.role == "employee":
        if principal.employee_ref is None:
            raise RetrievalDenied("employee principal has no employee_ref")

        if principal.clearance == Clearance.STANDARD.value:
            return {"employee_id": principal.employee_ref}
        if principal.clearance == Clearance.SENSITIVE.value:
            # A principal with no department is scoped to their own record
            # only — the "$or" department clause is omitted rather than
            # including a None value (Chroma has no null-equality semantics,
            # and including one risks silently widening or erroring instead
            # of scoping down).
            if principal.department is None:
                return {"employee_id": principal.employee_ref}
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
            ]}
        if principal.clearance == Clearance.PRIVILEGED.value:
            if principal.department is None:
                return {"$or": [
                    {"employee_id": principal.employee_ref},
                    {"department": {"$nin": ["HR", "Legal"]}},
                ]}
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
                {"department": {"$nin": ["HR", "Legal"]}},
            ]}
        raise RetrievalDenied(f"unrecognized clearance: {principal.clearance!r}")

    raise RetrievalDenied(f"unrecognized role: {principal.role!r}")


# Tools a guest must never reach, regardless of arguments -- guests may chat
# and file tickets (spec D9), but the people-collections and internal
# helpdesk operational state (workload) are staff-only. Every other
# allowed tool's real scoping already happens via retrieval_filter (search
# tools) or row-ownership in the tool's own query (list_my_tickets,
# get_ticket) -- authorize() does not need a bespoke rule for those.
_GUEST_DENIED_TOOLS = frozenset({"search_knowledge", "search_lessons", "get_helpdesk_workload"})


def authorize(principal: Principal, tool_name: str, arguments: dict) -> Allow | Deny:
    """The single tested chokepoint every tool call passes through before
    execution (spec section 6.3). `arguments` is accepted for a future
    argument-level rule but unused by the current rule set -- every present
    restriction is role-based only."""
    if principal.role == "guest" and tool_name in _GUEST_DENIED_TOOLS:
        return Deny(f"guests cannot use {tool_name!r}")
    return Allow()
