from __future__ import annotations

import uuid

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agent.guardrails import wrap_untrusted
from app.db.models import User
from app.rag.backend import get_rag_backend
from app.rbac.policy import Principal, RetrievalDenied, retrieval_filter


class SearchKnowledgeArgs(BaseModel):
    query: str
    scope: str = Field(default="auto")  # "employees" | "helpdesk" | "auto"
    k: int = Field(default=5, le=10)


class SearchLessonsArgs(BaseModel):
    query: str
    k: int = Field(default=5, le=5)


class GetMyProfileArgs(BaseModel):
    pass


async def search_knowledge_handler(principal: Principal, db: Session, args: SearchKnowledgeArgs) -> dict:
    collections = ["employees", "helpdesk"] if args.scope == "auto" else [args.scope]
    backend = get_rag_backend()
    wrapped_results: list[str] = []
    for collection in collections:
        try:
            where = retrieval_filter(principal, collection)
        except RetrievalDenied as exc:
            return {"is_error": True, "content": str(exc)}
        result = await backend.query(collection, args.query, where=where, k=args.k)
        for doc, metadata in zip(result["documents"], result["metadatas"]):
            source = f"{collection}/{metadata.get('source_file', 'unknown')}"
            wrapped_results.append(wrap_untrusted(source, doc))
    return {"results": wrapped_results}


async def search_lessons_handler(principal: Principal, db: Session, args: SearchLessonsArgs) -> dict:
    backend = get_rag_backend()
    result = await backend.query("lessons", args.query, where={}, k=args.k)
    wrapped_results = [
        wrap_untrusted(f"lessons/{metadata.get('lesson_id', 'unknown')}", doc)
        for doc, metadata in zip(result["documents"], result["metadatas"])
    ]
    # Lessons are advisory prior experience, framed as such -- never
    # instruction, subject to the same untrusted-content rules (spec 13).
    return {"lessons": wrapped_results}


async def get_my_profile_handler(principal: Principal, db: Session, args: GetMyProfileArgs) -> dict:
    if principal.kind != "user":
        return {"is_error": True, "content": "guests have no profile"}
    user = db.get(User, uuid.UUID(principal.user_id))
    if user is None:
        return {"is_error": True, "content": "profile not found"}
    return {
        "username": user.username, "role": user.role.value,
        "clearance": user.clearance.value if user.clearance else None,
        "department": user.department, "employee_ref": user.employee_ref,
        "helpdesk_ref": user.helpdesk_ref, "specialization": user.specialization,
    }
