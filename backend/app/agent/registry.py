from __future__ import annotations

import inspect
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from anthropic.types import ToolParam
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.agent.tools.approvals_and_attachments import (
    CreateApprovalRequestArgs, RequestAttachmentArgs, create_approval_request_handler, request_attachment_handler,
)
from app.agent.tools.knowledge import (
    GetMyProfileArgs, SearchKnowledgeArgs, SearchLessonsArgs,
    get_my_profile_handler, search_knowledge_handler, search_lessons_handler,
)
from app.agent.tools.routing import (
    FindHelpdeskSpecialistArgs, GetHelpdeskWorkloadArgs, find_helpdesk_specialist_handler, get_helpdesk_workload_handler,
)
from app.agent.tools.tickets import (
    CreateTicketArgs, GetTicketArgs, ListMyTicketsArgs, RecordTaskArgs,
    create_ticket_handler, get_ticket_handler, list_my_tickets_handler, record_task_handler,
)
from app.db.models import SpanKind
from app.rbac.policy import Deny, Principal, authorize
from app.tracing import span


class WebSearchArgs(BaseModel):
    """Placeholder input model for the `web_search` catalog entry below.
    web_search is a server tool (spec D3/8.3): Claude's API executes it
    directly and this backend never receives a tool_use block for it, so
    this model and the handler below exist only so `web_search` can occupy
    a normal TOOLS slot (spec 8.1's 12-tool catalog includes it) -- neither
    is ever actually invoked. to_anthropic_tool_params() special-cases this
    entry and serializes the real `web_search_20260209` server-tool dict
    instead of a schema built from this model."""

    query: str


async def _web_search_placeholder_handler(principal: Principal, db: Session, args: WebSearchArgs) -> dict:
    raise NotImplementedError(
        "web_search is a server tool; dispatch_tool() short-circuits on "
        "tool_name == 'web_search' before this handler is ever reached"
    )


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[..., Awaitable[dict]]
    kind: SpanKind = SpanKind.TOOL


# Deterministic order -- feeds the cache-prefix-stable `tools` array
# directly (spec 8.1: cache breakpoint stability requires this array to
# never silently reorder between requests).
TOOLS: list[ToolSpec] = [
    ToolSpec("search_knowledge", "Search the employee and/or helpdesk knowledge base, scoped to what you're permitted to see.", SearchKnowledgeArgs, search_knowledge_handler, SpanKind.RETRIEVAL),
    ToolSpec("search_lessons", "Search prior-resolution lessons for advisory guidance on similar past issues.", SearchLessonsArgs, search_lessons_handler, SpanKind.RETRIEVAL),
    ToolSpec("web_search", "Search the public web.", WebSearchArgs, _web_search_placeholder_handler),
    ToolSpec("get_my_profile", "Get your own user profile record.", GetMyProfileArgs, get_my_profile_handler),
    ToolSpec("list_my_tickets", "List tickets you have filed, optionally filtered by status.", ListMyTicketsArgs, list_my_tickets_handler),
    ToolSpec("get_ticket", "Get one ticket you own, are assigned to, or (as admin) any ticket.", GetTicketArgs, get_ticket_handler),
    ToolSpec("find_helpdesk_specialist", "Find the best-matching helpdesk specialist(s) for a described problem.", FindHelpdeskSpecialistArgs, find_helpdesk_specialist_handler),
    ToolSpec("get_helpdesk_workload", "Get current open/in-progress ticket counts for one or all helpdesk specialists.", GetHelpdeskWorkloadArgs, get_helpdesk_workload_handler),
    ToolSpec("request_attachment", "Ask the user to upload an image, PDF, or audio attachment.", RequestAttachmentArgs, request_attachment_handler),
    ToolSpec("record_task", "Record a classified problem as a task. Always call this once a problem is recognized.", RecordTaskArgs, record_task_handler),
    ToolSpec("create_ticket", "Create a ticket for a recorded task, assigned to a specialist.", CreateTicketArgs, create_ticket_handler),
    ToolSpec("create_approval_request", "File a request for a human administrator to approve a high-risk action. Never executes the action itself.", CreateApprovalRequestArgs, create_approval_request_handler),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}

# record_task's `evidence` and create_approval_request's `action_payload`
# are plain `dict` fields, deliberately open-ended (arbitrary diagnostic
# evidence; a payload shape that varies per action_type). Pydantic renders
# a bare `dict` field as `{"type": "object", "additionalProperties": true,
# ...}`, and `_pydantic_schema_to_strict` below only forces
# `additionalProperties: False` at the ROOT of the schema -- it does not
# walk into nested object properties, so this `true` survives into the
# serialized schema. Anthropic's `strict: true` tool-use mode requires
# `additionalProperties: false` on every object in the schema, including
# nested ones, so a truly open-ended dict field is structurally
# incompatible with strict mode -- there is no closed JSON schema that can
# describe "any object shape". These two tools are therefore served with
# `strict=False` instead of reworking their (already-reviewed) Pydantic
# models into JSON-string fields just to satisfy strict mode.
_NON_STRICT_TOOLS = frozenset({"record_task", "create_approval_request"})


def _pydantic_schema_to_strict(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema["additionalProperties"] = False
    return schema


def to_anthropic_tool_params() -> list[ToolParam | dict]:
    params: list[ToolParam | dict] = [
        ToolParam(
            name=spec.name, description=spec.description,
            input_schema=_pydantic_schema_to_strict(spec.input_model),
            strict=spec.name not in _NON_STRICT_TOOLS,
        )
        for spec in TOOLS
        if spec.name != "web_search"  # serialized as the real server-tool dict below instead
    ]
    # Server tool -- no handler, no input_model, declared directly as a
    # dict (spec D3 / 8.3). allowed_callers pinned to "direct" so this
    # doesn't silently provision code-execution-backed dynamic filtering
    # (web_search_20260209's default allowed_callers).
    params.append({
        "type": "web_search_20260209", "name": "web_search",
        "max_uses": 5, "allowed_callers": ["direct"],
    })
    return params


async def dispatch_tool(
    principal: Principal, db: Session, *, tool_name: str, tool_use_id: str, raw_input: str, extra_context: dict[str, Any],
) -> dict:
    """Validates arguments, authorizes, executes, and never raises -- every
    failure path returns an is_error tool_result dict instead (spec 8.3,
    8.5). `extra_context` carries per-call kwargs some handlers need beyond
    their Pydantic args (conversation_id, run_id, guest_email) -- filtered
    down to exactly the keyword-only arguments each specific handler's own
    signature declares, since not every tool's handler accepts the same
    ones (routing.py's and this file's handlers take none of them at all).

    On the success path, the return value is the handler's raw result
    dict, not a properly-shaped Anthropic `tool_result` content block (no
    `type`, no `tool_use_id`, no `is_error` unless the handler set one
    itself) -- the caller (a future agent loop) is responsible for wrapping
    this return value into an actual `tool_result` content block using the
    `tool_use_id` parameter already threaded through this function."""
    if tool_name == "web_search":
        return {"is_error": True, "content": "web_search is a server tool and should never be dispatched here"}

    spec = TOOLS_BY_NAME.get(tool_name)
    if spec is None:
        return {"is_error": True, "content": f"unknown tool {tool_name!r}"}

    try:
        args = spec.input_model.model_validate_json(raw_input)
    except (ValidationError, json.JSONDecodeError) as exc:
        return {"is_error": True, "content": f"invalid arguments: {exc}"}

    async with span(SpanKind.GUARDRAIL, "rbac.authorize") as recorder:
        decision = authorize(principal, tool_name, args.model_dump())
        recorder.metadata = {"tool_name": tool_name, "decision": type(decision).__name__}
    if isinstance(decision, Deny):
        return {"is_error": True, "content": decision.reason}

    # Keyword-only params only: handlers' positional parameters are always
    # (principal, db, args) -- including those in the filter set (as a
    # plain `set(...parameters)` would) risks `got multiple values for
    # argument` if an extra_context key ever collided with one of those
    # names, instead of being safely filtered out.
    accepted_params = {
        name for name, param in inspect.signature(spec.handler).parameters.items()
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }
    handler_kwargs = {k: v for k, v in extra_context.items() if k in accepted_params}

    try:
        async with span(spec.kind, tool_name) as recorder:
            recorder.input = args.model_dump()
            result = await spec.handler(principal, db, args, **handler_kwargs)
            recorder.output = result
    except Exception as exc:
        return {"is_error": True, "content": f"tool execution failed: {exc}"}
    return result
