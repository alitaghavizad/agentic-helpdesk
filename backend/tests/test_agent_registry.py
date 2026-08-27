from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

from app.agent.registry import TOOLS, dispatch_tool, to_anthropic_tool_params
from app.db.models import Conversation
from app.rbac.policy import Principal

_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)
_EMPLOYEE = Principal(kind="user", user_id="00000000-0000-0000-0000-000000000001", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)

_FORBIDDEN_TOOL_NAMES = {
    "send_email", "grant_access", "grant_system_access", "update_user_clearance",
    "reassign_ticket_cross_department", "cross_department_ticket_assignment", "reset_credential", "run_sql", "execute_sql",
}


@pytest.fixture(autouse=True)
def _traced_run(cleanup_run):
    """dispatch_tool always wraps rbac.authorize (and, when authorized, the
    handler itself) in a tracing span; span() hard-requires an active run
    and raises RuntimeError otherwise (see app/tracing/spans.py's module
    docstring and tests/test_agent_tools_routing.py's identical fixture)."""
    from app.db.models import RunStatus, RunTrigger
    from app.tracing import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        yield
        end_run(handle, status=RunStatus.OK)
    except Exception:
        end_run(handle, status=RunStatus.ABORTED)
        raise
    finally:
        cleanup_run(handle.run_id)


def test_tool_catalog_has_exactly_the_twelve_buildable_tools():
    names = {t.name for t in TOOLS}
    assert names == {
        "search_knowledge", "search_lessons", "web_search", "get_my_profile",
        "list_my_tickets", "get_ticket", "find_helpdesk_specialist", "get_helpdesk_workload",
        "request_attachment", "record_task", "create_ticket", "create_approval_request",
    }
    assert "parse_attachment" not in names  # genuinely blocked this phase (Phase 7)


def test_serialized_tool_catalog_contains_no_forbidden_tool_names():
    params = to_anthropic_tool_params()
    serialized_names = {p["name"] if isinstance(p, dict) else p.name for p in params}
    assert serialized_names.isdisjoint(_FORBIDDEN_TOOL_NAMES)


_NON_STRICT_TOOLS = {"record_task", "create_approval_request"}


def test_serialized_tool_catalog_is_strict_with_no_additional_properties():
    for param in to_anthropic_tool_params():
        p = param if isinstance(param, dict) else param.model_dump()
        if p.get("type", "custom") not in ("custom", None):
            continue  # server tools (web_search) don't carry input_schema/strict
        # additionalProperties: False at the root holds for every custom
        # tool regardless of strict -- it's still correct (and still
        # rejects unexpected top-level keys) even when strict is False.
        assert p["input_schema"].get("additionalProperties") is False
        if p["name"] in _NON_STRICT_TOOLS:
            assert p.get("strict") is False
            continue
        assert p.get("strict") is True


def test_record_task_and_create_approval_request_are_the_only_non_strict_tools():
    # RecordTaskArgs.evidence and CreateApprovalRequestArgs.action_payload
    # are plain, intentionally open-ended `dict` fields -- Pydantic renders
    # those as a nested `additionalProperties: true`, which strict mode's
    # requirement (every object in the schema, not just the root, must be
    # closed) can never satisfy. Every other custom tool has no such field
    # and must stay strict.
    params = to_anthropic_tool_params()
    by_name = {p["name"] if isinstance(p, dict) else p.name: (p if isinstance(p, dict) else p.model_dump()) for p in params}

    for name in _NON_STRICT_TOOLS:
        assert by_name[name].get("strict") is False

    other_custom_tools = {
        name for name, p in by_name.items()
        if p.get("type", "custom") in ("custom", None) and name not in _NON_STRICT_TOOLS
    }
    # 12 total tools, minus web_search (a server tool dict, not a custom
    # schema, filtered out above), minus the 2 known non-strict tools.
    assert len(other_custom_tools) == 9
    for name in other_custom_tools:
        assert by_name[name].get("strict") is True


def test_to_anthropic_tool_params_serializes_web_search_exactly_once():
    # web_search is both a real ToolSpec in TOOLS (so TOOLS itself satisfies
    # the "all 12 tools" catalog) AND explicitly excluded from the
    # Pydantic-schema loop in to_anthropic_tool_params() in favor of the
    # real server-tool dict appended separately. If a future edit ever
    # dropped that exclusion, web_search would silently serialize twice --
    # once as a broken custom-tool schema, once as the real server tool.
    params = to_anthropic_tool_params()
    names = [p["name"] if isinstance(p, dict) else p.name for p in params]
    assert len(names) == 12
    assert len(set(names)) == 12
    assert names.count("web_search") == 1


async def test_dispatch_tool_denies_guest_for_search_knowledge(db_session):
    result = await dispatch_tool(_GUEST, db=db_session, tool_name="search_knowledge", tool_use_id="t1", raw_input='{"query": "x"}', extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_writes_an_audit_row_when_authorize_denies(db_session):
    """Spec 6.3: a Deny produces an is_error tool_result, an audit_log row,
    AND a denied span. Phase 4 shipped the first and third; this covers the
    audit row. dispatch_tool must commit it -- nothing else will, since a
    denial short-circuits before any handler (and therefore any commit) runs."""
    from app.db.models import ActorType, AuditLog

    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="search_knowledge", tool_use_id="t1",
        raw_input='{"query": "x"}', extra_context={},
    )
    assert result["is_error"] is True

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "tool.denied", AuditLog.target_id == "search_knowledge",
    ).one()
    assert row.actor_type == ActorType.USER
    assert row.target_type == "tool"
    assert "guests cannot use" in row.payload["reason"]


async def test_dispatch_tool_still_returns_the_denial_when_the_audit_write_fails(db_session, monkeypatch, capsys):
    """Spec 6.3: "The loop continues -- a denial is information for the
    agent, not a crash", and dispatch_tool's own docstring promises it never
    raises. So a failed audit_log write (connectivity blip, constraint
    violation, a session left broken by earlier use) must degrade to a
    warning, not propagate out through loop.py's `except BaseException`,
    which would end the run as RunStatus.ERROR and kill the whole turn with
    a generic "internal error occurred"."""
    def _boom(*args, **kwargs):
        raise RuntimeError("audit table is on fire")

    monkeypatch.setattr("app.agent.registry.record_audit", _boom)

    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="search_knowledge", tool_use_id="t1",
        raw_input='{"query": "x"}', extra_context={},
    )
    # The denial itself is what has to survive -- unchanged, and not
    # replaced by an "audit failed" error result.
    assert result["is_error"] is True
    assert "guests cannot use" in result["content"]
    # ...but the swallowed failure must not be silent.
    assert "audit table is on fire" in capsys.readouterr().err


async def test_dispatch_tool_returns_error_for_invalid_json_arguments(db_session):
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="get_my_profile", tool_use_id="t1", raw_input="not json", extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_returns_error_for_unknown_tool(db_session):
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="totally_made_up_tool", tool_use_id="t1", raw_input="{}", extra_context={})
    assert result["is_error"] is True


async def test_dispatch_tool_filters_extra_context_to_handlers_own_parameters(db_session):
    # get_helpdesk_workload_handler accepts ONLY (principal, db, args) -- if
    # extra_context isn't filtered by the handler's own signature, this
    # raises TypeError instead of returning a result.
    result = await dispatch_tool(
        _EMPLOYEE, db=db_session, tool_name="get_helpdesk_workload", tool_use_id="t1",
        raw_input="{}", extra_context={"conversation_id": "00000000-0000-0000-0000-000000000099", "run_id": "00000000-0000-0000-0000-000000000098", "guest_email": None},
    )
    assert result.get("is_error") is not True


async def test_dispatch_tool_actually_passes_extra_context_values_through_to_the_handler(db_session):
    # The test above only proves get_helpdesk_workload_handler (which
    # accepts ZERO extra kwargs) doesn't blow up -- it would pass even if
    # handler_kwargs were hardcoded to {} for every tool. Prove the
    # opposite here: dispatch create_approval_request (whose handler
    # requires conversation_id as a keyword-only arg) and confirm the
    # created row's conversation_id actually matches the value passed
    # through extra_context, not just that no TypeError was raised.
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    raw_input = json.dumps({
        "action_type": "send_email", "action_payload": {"to": "hr@northstar.example"},
        "justification": "Needs a copy of their offer letter.", "risk_level": "low",
        "agent_summary": "Requesting email send.",
    })
    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="create_approval_request", tool_use_id="t1",
        raw_input=raw_input, extra_context={"conversation_id": conv.id},
    )
    assert result.get("is_error") is not True
    assert "request_number" in result

    from app.db.models import ApprovalRequest

    request = db_session.query(ApprovalRequest).filter(
        ApprovalRequest.request_number == int(result["request_number"].removeprefix("REQ-"))
    ).one()
    assert request.conversation_id == conv.id


async def test_dispatch_tool_converts_handler_exception_to_is_error(db_session, monkeypatch):
    import app.agent.registry as registry_module

    async def _boom(principal, db, args):
        raise RuntimeError("simulated handler failure")

    # ToolSpec is unconditionally frozen=True, so plain monkeypatch.setattr
    # on an existing ToolSpec instance always raises FrozenInstanceError --
    # substituting a handler for a test means swapping the TOOLS_BY_NAME
    # entry itself for a dataclasses.replace()'d copy instead.
    monkeypatch.setattr(
        registry_module, "TOOLS_BY_NAME",
        {**registry_module.TOOLS_BY_NAME, "get_my_profile": dataclasses.replace(registry_module.TOOLS_BY_NAME["get_my_profile"], handler=_boom)},
    )
    result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="get_my_profile", tool_use_id="t1", raw_input="{}", extra_context={})
    assert result["is_error"] is True
    assert "simulated handler failure" in result["content"]


async def test_dispatch_tool_rolls_back_session_after_a_real_db_flush_failure(db_session):
    """record_task's category/severity args are now constrained to
    TaskCategory/Severity enums (RecordTaskArgs), so a bad value there is
    now caught at Pydantic validation, before any handler runs -- that hole
    is closed. But conversation_id/run_id are still plain extra_context
    kwargs (spec: the model must never supply conversation_id itself, so
    it's server-injected, unmediated by Pydantic), so a caller providing a
    run_id that doesn't correspond to a real Run row still only fails at
    db.commit()'s FK check, not at argument validation. Without an explicit
    db.rollback() in dispatch_tool's except branch, SQLAlchemy leaves the
    session "inactive" after the failed flush, and the *next* operation on
    the same session (e.g. saving the assistant's reply later in the same
    chat turn) raises PendingRollbackError instead of proceeding -- silently
    breaking the rest of the turn. Proves the session is still usable
    immediately after the is_error return."""
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    raw_input = json.dumps({
        "title": "Broken VPN", "category": "vpn_network",
        "severity": "medium", "summary": "s", "affected_systems": [],
    })
    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="record_task", tool_use_id="t1",
        raw_input=raw_input,
        extra_context={"conversation_id": conv.id, "run_id": uuid.uuid4(), "guest_email": conv.guest_email},
    )
    assert result["is_error"] is True

    # If dispatch_tool didn't roll back, this next statement on the same
    # session raises PendingRollbackError instead of succeeding.
    db_session.add(Conversation(guest_name="Still Works", guest_email="still-works@example.com"))
    db_session.commit()


async def test_record_task_ignores_a_model_supplied_conversation_id_and_uses_server_context(db_session):
    """Root-cause regression for the live-API gate failure this task fixes:
    the model invented conversation_id="conv-emp5b1-vpn" (not a UUID, and
    not even the real conversation) because RecordTaskArgs used to declare
    conversation_id as a model-supplied field, and record_task_handler
    called uuid.UUID(args.conversation_id) on it unguarded -- six retries,
    all `ValueError: badly formed hexadecimal UUID string`, no Task and no
    ticket ever written (spec 18's gate).

    conversation_id is now exclusively a keyword-only parameter on
    record_task_handler, sourced from dispatch_tool's extra_context (the
    same server-injected mechanism create_ticket_handler already used) --
    never a Pydantic field the model can populate. This test replays the
    exact fabricated string from the incident inside the raw tool-call
    JSON and proves it has no effect at all: Pydantic silently drops the
    unknown key, and the resulting Task is attached to the real,
    server-supplied conversation.

    Against the pre-fix code (conversation_id required on RecordTaskArgs,
    uuid.UUID(args.conversation_id) in the handler), this exact raw_input
    fails every time with is_error=True / "badly formed hexadecimal UUID
    string" and never reaches db.commit() -- so this test would fail
    (result["is_error"] would be True, task would be None) against that
    code.
    """
    from app.db.models import Run, RunStatus, RunTrigger, Task

    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    other_conv = Conversation(guest_name="Other", guest_email="other@example.com")
    db_session.add_all([conv, other_conv])
    # The Run backing classified_by_run_id is created through db_session, NOT
    # tracing.start_run(). start_run() commits the Run on its own connection,
    # and the Task that record_task inserts through db_session then holds an
    # FK share lock on that row -- so cleanup_run's separate-connection
    # DELETE FROM runs blocks on db_session's still-open transaction and
    # deadlocks until one side is killed. (This is not hypothetical: an
    # earlier draft of this test hung a suite run for nine hours.) Creating
    # the Run here keeps it in the same transaction as the Task, so both roll
    # back together and no cleanup_run is needed. The span machinery still
    # has an active run -- this module's autouse _traced_run fixture supplies
    # it -- and that run is never FK-referenced by anything here.
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()
    db_session.refresh(conv)
    db_session.refresh(other_conv)
    db_session.refresh(run)

    raw_input = json.dumps({
        # the literal fabricated, non-UUID id from the live-API incident --
        # also deliberately not other_conv's real id either, to prove a
        # model can't hijack another conversation's id even when it happens
        # to supply a well-formed one.
        "conversation_id": "conv-emp5b1-vpn",
        "title": "VPN client times out", "category": "vpn_network",
        "severity": "medium", "summary": "s", "affected_systems": [],
    })
    result = await dispatch_tool(
        _GUEST, db=db_session, tool_name="record_task", tool_use_id="t1",
        raw_input=raw_input,
        extra_context={"conversation_id": conv.id, "run_id": run.id, "guest_email": conv.guest_email},
    )

    assert result.get("is_error") is not True, result
    task = db_session.get(Task, uuid.UUID(result["task_id"]))
    assert task is not None
    assert task.conversation_id == conv.id
    assert task.conversation_id != other_conv.id


async def test_dispatch_tool_failed_handler_span_records_error_status(db_session, monkeypatch, cleanup_run):
    # dispatch_tool wraps the WHOLE `async with span(...)` block (not just
    # the `await spec.handler(...)` call) in its outer try/except, on the
    # claim that the span's own __aexit__ still observes the exception and
    # records SpanStatus.ERROR before dispatch_tool catches the re-raised
    # exception and converts it to an is_error dict. Nothing else tests
    # this claim -- a future refactor could move the try inside the `async
    # with` block, which would still pass the test above but silently
    # record SpanStatus.OK for every failed tool call. Open our own run
    # (nested inside this file's autouse _traced_run) so we can inspect
    # its spans via trace_tree after end_run flushes them, instead of
    # relying on the fixture's own run which gets deleted by cleanup_run
    # before the test body can look at it.
    import app.agent.registry as registry_module
    from app.db.models import RunStatus, RunTrigger
    from app.tracing import end_run, start_run
    from app.tracing.store import trace_tree

    async def _boom(principal, db, args):
        raise RuntimeError("simulated handler failure")

    monkeypatch.setattr(
        registry_module, "TOOLS_BY_NAME",
        {**registry_module.TOOLS_BY_NAME, "get_my_profile": dataclasses.replace(registry_module.TOOLS_BY_NAME["get_my_profile"], handler=_boom)},
    )

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        result = await dispatch_tool(_EMPLOYEE, db=db_session, tool_name="get_my_profile", tool_use_id="t1", raw_input="{}", extra_context={})
        assert result["is_error"] is True
        end_run(handle, status=RunStatus.OK)

        trace = trace_tree(handle.run_id)
        tool_spans = [node.span for node in trace.roots if node.span.name == "get_my_profile"]
        assert len(tool_spans) == 1
        assert tool_spans[0].status.value == "error"
        assert "simulated handler failure" in tool_spans[0].error
    finally:
        cleanup_run(handle.run_id)
