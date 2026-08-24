from __future__ import annotations

from dataclasses import dataclass

from app.db.models import Clearance

# The "helpdesk" collection is chunked by Markdown `##` heading at ingest
# time (Phase 2 plan). "Routing guidance" is the heading name in every
# helpdesk profile that carries routing-relevant information without
# exposing the full support playbook. This constant is verified against
# real ingested `section` metadata by the Phase 2 retrieval-eval gate.
RESTRICTED_HELPDESK_SECTIONS = {"Routing guidance"}

ACCESS_CLASSIFICATION_MAP = {
    "Standard": Clearance.STANDARD,
    "Sensitive business-data access": Clearance.SENSITIVE,
    "Privileged production access with approval": Clearance.PRIVILEGED,
}


class RetrievalDenied(PermissionError):
    """Raised when a principal has no retrieval access to a collection at all."""


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "guest"
    user_id: str | None
    role: str  # "guest" | "employee" | "helpdesk" | "admin"
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None


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
        if principal.clearance == Clearance.STANDARD.value:
            return {"employee_id": principal.employee_ref}
        if principal.clearance == Clearance.SENSITIVE.value:
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
            ]}
        if principal.clearance == Clearance.PRIVILEGED.value:
            return {"$or": [
                {"employee_id": principal.employee_ref},
                {"department": principal.department},
                {"department": {"$nin": ["HR", "Legal"]}},
            ]}
        raise RetrievalDenied(f"unrecognized clearance: {principal.clearance!r}")

    raise RetrievalDenied(f"unrecognized role: {principal.role!r}")
