"""Response models for the admin API.

These exist for a specific consumer: phase 8b builds its screens against
this API's OpenAPI schema. Until this module existed, `PageResponse.items`
was `list[dict]` and `/overview`, `/costs` and `/trace` had no response
model at all -- so half the read surface published an empty schema, and a
generated client could not tell `cost_usd` from `cache_hit_rate`.

They are declarations, not transformations: the router still builds each
payload explicitly, field by field, so that a column cannot join the API
by being added to a table. What these add is a schema for that payload and
validation that the router's dict actually matches it.

Kept out of router.py so that module stays readable as a list of routes;
the mapping from ORM row to payload still lives there.
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class PageResponse(BaseModel, Generic[ItemT]):
    """One envelope for every list endpoint. `total` is the count BEFORE
    limit/offset, so a client can render a pager without walking the whole
    result set.

    Generic so each endpoint publishes its own item shape:
    `response_model=PageResponse[RunSummary]`. The unparameterised form
    still works and still means `items: list[Any]`, which is what the
    endpoints looked like before -- so a route that forgets to parameterise
    it degrades to the old behaviour rather than breaking.
    """
    items: list[ItemT]
    total: int
    limit: int
    offset: int


class RunSummary(BaseModel):
    """GET /runs. Deliberately narrower than TraceRun below: the list needs
    enough to render a row, not the token breakdown."""
    id: str
    trigger: str
    status: str
    started_at: str | None
    duration_ms: int | None
    cost_usd: float | None
    llm_calls: int | None
    tool_calls: int | None
    error: str | None


class ConversationSummary(BaseModel):
    id: str
    title: str | None
    status: str
    user_id: str | None
    guest_name: str | None
    guest_email: str | None
    created_at: str | None


class AuditEntry(BaseModel):
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    payload: dict[str, Any] | None
    ip_address: str | None
    created_at: str | None


class UserSummary(BaseModel):
    id: str
    username: str
    email: str
    full_name: str
    role: str
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None
    is_active: bool
    dev_seed: bool


class LessonSummary(BaseModel):
    id: str
    title: str
    category: str
    content_md: str
    status: str
    confidence: str
    ticket_id: str | None
    created_at: str | None


class Overview(BaseModel):
    """GET /overview. `error_rate` is a fraction of today's COMPLETED runs,
    not of every run -- in-flight ones have no outcome yet to be wrong."""
    runs_today: int
    spend_today: float
    pending_approvals: int
    open_tickets: int
    error_rate: float


class CostByDay(BaseModel):
    day: str
    cost_usd: float


class CostByModel(BaseModel):
    model: str
    cost_usd: float
    calls: int


class CostByUser(BaseModel):
    username: str
    cost_usd: float


class CostByTrigger(BaseModel):
    trigger: str
    cost_usd: float
    runs: int


class CostTotals(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    cache_hit_rate: float


class Costs(BaseModel):
    by_day: list[CostByDay]
    by_model: list[CostByModel]
    by_user: list[CostByUser]
    by_trigger: list[CostByTrigger]
    totals: CostTotals


class TraceRun(BaseModel):
    """The run header on GET /runs/{id}/trace -- the full token breakdown,
    unlike RunSummary."""
    id: str
    trigger: str
    status: str
    started_at: str | None
    duration_ms: int | None
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    error: str | None


class SpanNode(BaseModel):
    """One node of the waterfall. `input`/`output` are already redacted at
    persistence time by tracing/redaction.py; this does not re-redact and
    must not be relied on to."""
    id: str
    kind: str
    name: str
    status: str
    duration_ms: int | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    input: Any | None
    output: Any | None
    error: str | None
    children: list["SpanNode"]


class RunTrace(BaseModel):
    """`truncated` is why this is not just {run, roots}.

    A trace is unbounded in the data: one run's tree measured 167,617 bytes
    here, and nothing stops a longer agentic run being far larger. Spans
    beyond `span_cap` are dropped and the flag says so, because a silently
    shortened waterfall is worse than a short one -- an admin reading it
    would conclude the run simply stopped there.
    """
    run: TraceRun
    roots: list[SpanNode]
    span_count: int
    truncated: bool
