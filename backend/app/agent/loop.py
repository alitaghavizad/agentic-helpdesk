from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, AsyncIterator

from sqlalchemy.orm import Session

from app.agent.budget import AbortRun, check_and_record_usage, agent_enabled, new_turn_budget
from app.agent.guardrails import check_inbound
from app.agent.prompts import build_system_prompt
from app.agent.registry import dispatch_tool, to_anthropic_tool_params
from app.db.models import RunStatus, RunTrigger, SpanKind
from app.rbac.policy import Principal
from app.tracing import current_span, end_run, span, start_run
from app.tracing.pricing import cost_for


@dataclass
class TurnEvent:
    type: str
    data: dict


def _text_from(response) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


def _tool_uses_from(response) -> list:
    return [block for block in response.content if block.type == "tool_use"]


async def _call_model(client, *, model: str, max_tokens: int, system: str, messages: list, tools: list):
    async with client.beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        thinking={"type": "adaptive", "display": "summarized"},
        output_config={"effort": "high"},
        tools=tools,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=messages,
    ) as stream:
        async for _event in stream:
            pass  # no frontend consumes token-level deltas yet (no frontend exists this phase)
        return await stream.get_final_message()


async def run_turn(
    client: Any,
    db: Session,
    principal: Principal,
    *,
    conversation_id: uuid.UUID,
    user_key: str,
    history: list[dict],
    user_message: str,
) -> AsyncIterator[TurnEvent]:
    """The main iteration loop (spec 8.5). `client` is any object exposing
    `.beta.messages.stream(...)` the way anthropic.AsyncAnthropic does --
    the real client in production, tests/support/fake_anthropic.FakeAnthropicClient
    in tests. Never raises: every failure path (budget breach, disabled
    agent, tool error) becomes an `error` TurnEvent instead."""
    if not agent_enabled():
        yield TurnEvent("error", {"message": "The agent is currently disabled by an administrator."})
        return

    handle = start_run(RunTrigger.CHAT_TURN, conversation_id=conversation_id, user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None)
    budget = new_turn_budget()
    messages = list(history) + [{"role": "user", "content": user_message}]
    system_prompt = build_system_prompt(principal)
    tools = to_anthropic_tool_params()
    aborted_reason: str | None = None

    try:
        await check_inbound(user_message)

        while True:
            try:
                budget.check()
            except AbortRun as exc:
                aborted_reason = exc.reason
                yield TurnEvent("error", {"message": f"Turn ended: {exc.reason}."})
                break

            async with span(SpanKind.LLM, "beta.messages.stream") as recorder:
                response = await _call_model(
                    client, model="claude-opus-5", max_tokens=16000,
                    system=system_prompt, messages=messages, tools=tools,
                )
                usage = response.usage
                recorder.record_usage(
                    model=response.model,
                    input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens or 0,
                    cache_write_tokens=usage.cache_creation_input_tokens or 0,
                )
                turn_cost = cost_for(
                    response.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_input_tokens or 0, cache_write_tokens=usage.cache_creation_input_tokens or 0,
                )

            try:
                check_and_record_usage(user_key, tokens=usage.input_tokens + usage.output_tokens, cost=turn_cost or Decimal("0"))
            except AbortRun as exc:
                aborted_reason = exc.reason
                yield TurnEvent("error", {"message": f"Turn ended: {exc.reason}."})
                break

            budget.record_iteration(turn_cost)

            if response.stop_reason == "refusal":
                yield TurnEvent("error", {"message": "The request was refused and no fallback was available."})
                break

            text = _text_from(response)
            if text:
                yield TurnEvent("token", {"text": text})

            messages.append({"role": "assistant", "content": response.content})

            tool_uses = _tool_uses_from(response)
            if not tool_uses:
                break

            async def _run_one(tool_use):
                extra_context = {"conversation_id": conversation_id, "run_id": handle.run_id, "guest_email": None if principal.kind == "user" else user_key}
                return await dispatch_tool(
                    principal, db, tool_name=tool_use.name, tool_use_id=tool_use.id,
                    raw_input=json.dumps(tool_use.input), extra_context=extra_context,
                )

            for tool_use in tool_uses:
                yield TurnEvent("tool_start", {"name": tool_use.name, "id": tool_use.id})

            tool_results = await asyncio.gather(*(_run_one(t) for t in tool_uses))

            results = []
            for tool_use, result in zip(tool_uses, tool_results):
                yield TurnEvent("tool_end", {"name": tool_use.name, "id": tool_use.id, "is_error": result.get("is_error", False)})
                if tool_use.name == "record_task" and not result.get("is_error"):
                    yield TurnEvent("task_recorded", result)
                if tool_use.name == "create_ticket" and not result.get("is_error"):
                    yield TurnEvent("ticket_created", result)
                if tool_use.name == "create_approval_request" and not result.get("is_error"):
                    yield TurnEvent("approval_requested", result)
                if tool_use.name == "request_attachment" and not result.get("is_error"):
                    yield TurnEvent("attachment_request", result)
                results.append({
                    "type": "tool_result", "tool_use_id": tool_use.id,
                    "content": json.dumps(result), "is_error": result.get("is_error", False),
                })

            messages.append({"role": "user", "content": results})

        end_run(handle, status=RunStatus.ABORTED if aborted_reason else RunStatus.OK, error=aborted_reason)
    except BaseException as exc:
        end_run(handle, status=RunStatus.ERROR, error=str(exc))
        yield TurnEvent("error", {"message": "An internal error occurred."})
        raise
    finally:
        yield TurnEvent("done", {"run_id": str(handle.run_id)})
