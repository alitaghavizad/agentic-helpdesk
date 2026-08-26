from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ActorType, AuditLog
from app.rbac.policy import Principal


def actor_from_principal(principal: Principal) -> tuple[ActorType, str | None]:
    """A guest is still a human actor (ActorType.USER), identified by the
    contact email their JWT carries -- guests are deliberately not rows in
    `users` (spec 5.1), so there is no id to record. ActorType.AGENT is for
    actions the model takes on its own behalf; ActorType.SYSTEM is for
    unattended jobs."""
    if principal.kind == "guest":
        return ActorType.USER, principal.guest_email
    return ActorType.USER, principal.user_id


def record_audit(
    db: Session,
    *,
    actor_type: ActorType,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    payload: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """The ONLY write path to `audit_log`, which spec 5.4 defines as
    append-only: no update or delete helper exists in this module and none
    may be added.

    Stages the row and flushes (so `.id` is populated) but deliberately does
    NOT commit -- the row belongs to the caller's transaction, so an audit
    entry can never survive a mutation that was rolled back. This is the
    opposite of app/tracing/store.py, which commits on its own connection
    precisely so a trace outlives a failed business transaction.
    """
    row = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload if payload is not None else {},
        ip_address=ip_address,
    )
    db.add(row)
    db.flush()
    return row
