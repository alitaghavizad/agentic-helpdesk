from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import Severity, TaskCategory
from app.rbac.policy import Principal
from app.tickets.routing import open_workload, rank_specialists, workload_by_specialist


class FindHelpdeskSpecialistArgs(BaseModel):
    # find_helpdesk_specialist is a *strict* tool (see
    # app/agent/registry.py's _NON_STRICT_TOOLS docstring) -- strict mode
    # forbids $ref/$defs anywhere in the schema, so these are typed
    # Literal[...] (Pydantic renders that as an inline `enum` array) rather
    # than the TaskCategory/Severity enum classes themselves (which commit
    # 44e4cf1 used for RecordTaskArgs, but that tool is deliberately
    # non-strict). Without this, a miscased model value like
    # severity="Critical" or category="security_Incident" passed straight
    # through to rank_specialists(), whose escalation hard filter (spec
    # 8.4) compares against lowercase literals and would silently skip a
    # critical/security ticket past the filter instead of rejecting it.
    problem_summary: str
    category: Literal[tuple(c.value for c in TaskCategory)]
    severity: Literal[tuple(s.value for s in Severity)] = "medium"


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
