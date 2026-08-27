from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import EscalationAuthority, Role, Ticket, TicketStatus, User
from app.rag.backend import get_rag_backend

ESCALATING_SEVERITIES = frozenset({"high", "critical"})
OPEN_STATUSES = (TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)

# A concrete, monotonic scoring formula (spec 8.4's three signals): semantic
# rank dominates (the 25 specializations are distinct/single-holder, so
# rank position is the strongest signal), workload penalizes a busy
# specialist without ever overriding a clearly-better semantic match or the
# escalation-authority hard filter, which runs before scoring, not as a
# score component.
SEMANTIC_RANK_PENALTY = 0.15
WORKLOAD_PENALTY = 0.05


def collapse_to_unique_helpdesk_ids(query_result) -> list[str]:
    """Chunks are ranked by ascending distance; keep first-seen (best) order
    per unique helpdesk_id, discarding later, worse-ranked chunks of a
    document already seen -- the same document-collapsing principle
    scripts/eval_retrieval.py uses for the qrels-comparable Recall metrics."""
    seen: list[str] = []
    for metadata in query_result["metadatas"]:
        helpdesk_id = metadata.get("helpdesk_id")
        if helpdesk_id and helpdesk_id not in seen:
            seen.append(helpdesk_id)
    return seen


def open_workload(db: Session, helpdesk_ref: str) -> int:
    """Spec 8.4's 'live load' signal: open + in-progress tickets for one
    specialist."""
    return db.query(Ticket).filter(
        Ticket.assignee_helpdesk_ref == helpdesk_ref,
        Ticket.status.in_(OPEN_STATUSES),
    ).count()


def workload_by_specialist(db: Session) -> dict[str, int]:
    rows = db.query(Ticket.assignee_helpdesk_ref).filter(Ticket.status.in_(OPEN_STATUSES)).all()
    counts: dict[str, int] = {}
    for (ref,) in rows:
        counts[ref] = counts.get(ref, 0) + 1
    return counts


async def rank_specialists(
    db: Session, *, problem_summary: str, category: str, severity: str, limit: int = 3,
) -> list[dict]:
    """Spec 8.4: combine semantic match, live load, and escalation fit, and
    return the top `limit` candidates with a score breakdown. Escalation fit
    is a hard filter applied before scoring -- candidates lacking `high`
    escalation authority for a high/critical severity or a security_incident
    are removed entirely, never merely down-ranked. Shift is returned as
    informational metadata and does not affect the score."""
    backend = get_rag_backend()
    result = await backend.query("helpdesk", problem_summary, where={}, k=15)
    ranked_ids = collapse_to_unique_helpdesk_ids(result)

    candidates: list[dict] = []
    for rank, helpdesk_ref in enumerate(ranked_ids):
        user = db.query(User).filter(User.role == Role.HELPDESK, User.helpdesk_ref == helpdesk_ref).one_or_none()
        if user is None:
            continue
        if severity in ESCALATING_SEVERITIES or category == "security_incident":
            if user.escalation_authority != EscalationAuthority.HIGH:
                continue
        workload = open_workload(db, helpdesk_ref)
        score = max(0.0, min(1.0, 1.0 - (rank * SEMANTIC_RANK_PENALTY) - (workload * WORKLOAD_PENALTY)))
        candidates.append({
            "helpdesk_ref": helpdesk_ref,
            "specialization": user.specialization,
            "shift": user.shift,
            "escalation_authority": user.escalation_authority.value if user.escalation_authority else None,
            "current_workload": workload,
            "score": round(score, 4),
            "rationale": f"Semantic match rank {rank + 1}; current workload: {workload} open ticket(s).",
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[:limit]
