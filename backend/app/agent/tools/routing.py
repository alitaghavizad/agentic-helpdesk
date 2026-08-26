from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.rbac.policy import Principal
from app.tickets.routing import open_workload, rank_specialists, workload_by_specialist


class FindHelpdeskSpecialistArgs(BaseModel):
    problem_summary: str
    category: str
    severity: str = "medium"


class GetHelpdeskWorkloadArgs(BaseModel):
    helpdesk_ref: str | None = None


async def find_helpdesk_specialist_handler(principal: Principal, db: Session, args: FindHelpdeskSpecialistArgs) -> dict:
    candidates = await rank_specialists(
        db, problem_summary=args.problem_summary, category=args.category, severity=args.severity,
    )
    return {"candidates": candidates}


async def get_helpdesk_workload_handler(principal: Principal, db: Session, args: GetHelpdeskWorkloadArgs) -> dict:
    if args.helpdesk_ref is not None:
        return {"helpdesk_ref": args.helpdesk_ref, "open_and_in_progress": open_workload(db, args.helpdesk_ref)}
    return {"workload_by_specialist": workload_by_specialist(db)}
