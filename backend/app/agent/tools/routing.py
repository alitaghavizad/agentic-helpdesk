from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import EscalationAuthority, Role, Ticket, TicketStatus, User
from app.rag.backend import get_rag_backend
from app.rbac.policy import Principal

_ESCALATING_SEVERITIES = {"high", "critical"}
_OPEN_STATUSES = (TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)

# A concrete, monotonic scoring formula (spec 8.4's three signals): semantic
# rank dominates (the 25 specializations are distinct/single-holder, so
# rank position is the strongest signal), workload penalizes a busy
# specialist without ever overriding a clearly-better semantic match or the
# escalation-authority hard filter, which runs before scoring, not as a
# score component.
_SEMANTIC_RANK_PENALTY = 0.15
_WORKLOAD_PENALTY = 0.05


class FindHelpdeskSpecialistArgs(BaseModel):
    problem_summary: str
    category: str
    severity: str = "medium"


class GetHelpdeskWorkloadArgs(BaseModel):
    helpdesk_ref: str | None = None


def _collapse_to_unique_helpdesk_ids(query_result) -> list[str]:
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


async def find_helpdesk_specialist_handler(principal: Principal, db: Session, args: FindHelpdeskSpecialistArgs) -> dict:
    backend = get_rag_backend()
    result = await backend.query("helpdesk", args.problem_summary, where={}, k=15)
    ranked_ids = _collapse_to_unique_helpdesk_ids(result)

    candidates = []
    for rank, helpdesk_ref in enumerate(ranked_ids):
        user = db.query(User).filter(User.role == Role.HELPDESK, User.helpdesk_ref == helpdesk_ref).one_or_none()
        if user is None:
            continue
        if args.severity in _ESCALATING_SEVERITIES or args.category == "security_incident":
            if user.escalation_authority != EscalationAuthority.HIGH:
                continue
        workload = db.query(Ticket).filter(Ticket.assignee_helpdesk_ref == helpdesk_ref, Ticket.status.in_(_OPEN_STATUSES)).count()
        score = max(0.0, min(1.0, 1.0 - (rank * _SEMANTIC_RANK_PENALTY) - (workload * _WORKLOAD_PENALTY)))
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
    return {"candidates": candidates[:3]}


async def get_helpdesk_workload_handler(principal: Principal, db: Session, args: GetHelpdeskWorkloadArgs) -> dict:
    query = db.query(Ticket).filter(Ticket.status.in_(_OPEN_STATUSES))
    if args.helpdesk_ref is not None:
        query = query.filter(Ticket.assignee_helpdesk_ref == args.helpdesk_ref)
        return {"helpdesk_ref": args.helpdesk_ref, "open_and_in_progress": query.count()}
    rows = db.query(Ticket.assignee_helpdesk_ref, Ticket.status).filter(Ticket.status.in_(_OPEN_STATUSES)).all()
    counts: dict[str, int] = {}
    for ref, _status in rows:
        counts[ref] = counts.get(ref, 0) + 1
    return {"workload_by_specialist": counts}
