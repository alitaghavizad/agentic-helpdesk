from __future__ import annotations

import uuid

from app.db.models import Ticket
from app.rbac.policy import Principal


def scope_tickets_query(query, principal: Principal, *, guest_email: str | None = None):
    """Spec 6.4: filter on requester identity for employees, on assignee for
    helpdesk, unrestricted for admin. This is the ONLY place that rule is
    written -- both the agent tools and the HTTP endpoints route through it,
    so the two can never drift apart.

    Fails closed by construction: every branch either returns a filtered
    query or (admin only) the unfiltered one, and a principal missing the
    identifier its branch needs matches zero rows rather than falling
    through to an unfiltered query.
    """
    if principal.role == "admin":
        return query
    if principal.role == "helpdesk":
        # helpdesk_ref None -> `Ticket.assignee_helpdesk_ref == None` -> no rows.
        return query.filter(Ticket.assignee_helpdesk_ref == principal.helpdesk_ref)
    if principal.kind == "guest":
        email = guest_email if guest_email is not None else principal.guest_email
        return query.filter(Ticket.requester_guest_email == email)
    if principal.user_id is None:
        return query.filter(Ticket.id.is_(None))
    return query.filter(Ticket.requester_user_id == uuid.UUID(principal.user_id))


def can_read_ticket(principal: Principal, ticket: Ticket, *, guest_email: str | None = None) -> bool:
    """Single-row form of scope_tickets_query, for endpoints and tools that
    fetch a ticket by id. Kept deliberately parallel to the query version --
    a test asserts the two agree on the same rows."""
    if principal.role == "admin":
        return True
    if principal.role == "helpdesk":
        return principal.helpdesk_ref is not None and ticket.assignee_helpdesk_ref == principal.helpdesk_ref
    if principal.kind == "guest":
        email = guest_email if guest_email is not None else principal.guest_email
        return email is not None and ticket.requester_guest_email == email
    if principal.user_id is None:
        return False
    return ticket.requester_user_id == uuid.UUID(principal.user_id)
