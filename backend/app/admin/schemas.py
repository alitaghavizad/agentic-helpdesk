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

from app.chat.schemas import MessageView

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
    # Populated from the `users` row the admin queries already outer-join
    # for participant search (app/admin/queries.py's list_conversations) --
    # NULL for a guest conversation, which has no `users` row at all. Added
    # because rendering the raw `user_id` for a logged-in participant is
    # the one thing standing between an admin's search term ("jamie") and
    # ever seeing that name again in the result -- the same shape as
    # PrincipalResponse's username/full_name in app/auth/router.py, and for
    # the same reason.
    username: str | None
    full_name: str | None
    created_at: str | None


class AuditEntry(BaseModel):
    """`target_type`, `target_id` and `payload` are NOT NULL in the table, so
    they are not optional here. A published schema looser than the data makes
    every consumer write a null check for a case that cannot occur.
    `actor_id` and `ip_address` genuinely are nullable."""
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str
    target_id: str
    payload: dict[str, Any]
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
    # How many of `calls` are folded into `cost_usd` as an invisible $0 --
    # app/tracing/pricing.py's cost_for returns None for a model with no
    # rate, and queries.costs() coalesces the SUM of an all-NULL group to 0
    # rather than leaving the aggregate NULL (a SUM cannot represent
    # "unknown"). Without this count, that 0 is indistinguishable from a
    # model that genuinely cost nothing (spec 17's exact "confidently wrong
    # number" failure).
    unpriced_calls: int


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
    # Same signal as CostByModel.unpriced_calls, summed across every model:
    # how many LLM-call spans contributed $0 to `cost_usd` above not because
    # they were free, but because nothing prices them yet. A non-zero count
    # here means the total understates real spend.
    unpriced_calls: int


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


class ConversationDetail(BaseModel):
    """GET /conversations/{id}. Parent spec 15 wants the transcript beside
    its span tree, so this returns both halves in one call: the messages,
    and enough of each run to render a row that links into the trace view."""
    conversation: ConversationSummary
    messages: list[MessageView]
    runs: list[RunSummary]


class UserPatchResult(BaseModel):
    id: str
    role: str
    clearance: str | None


class LessonDeleteResult(BaseModel):
    """`archived` is redundant with `status == "archived"` but the router
    body already carries it (tests/test_admin_mutations.py asserts it
    verbatim), and a response_model must widen to fit an existing body
    rather than silently drop a field from it."""
    id: str
    status: str
    archived: bool
