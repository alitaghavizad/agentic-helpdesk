from __future__ import annotations

import itertools
from typing import Any

from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

_id_counter = itertools.count(1)


def make_usage(
    *,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> Usage:
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
    )


def make_text_message(
    *,
    text: str,
    model: str = "claude-opus-5",
    stop_reason: str = "end_turn",
    usage: Usage | None = None,
) -> Message:
    return Message(
        id=f"msg_{next(_id_counter)}",
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=usage or make_usage(),
    )


def make_tool_use_message(
    *,
    tool_name: str,
    tool_input: dict,
    tool_use_id: str,
    model: str = "claude-opus-5",
    usage: Usage | None = None,
) -> Message:
    return Message(
        id=f"msg_{next(_id_counter)}",
        type="message",
        role="assistant",
        model=model,
        content=[ToolUseBlock(type="tool_use", id=tool_use_id, name=tool_name, input=tool_input)],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=usage or make_usage(),
    )


class _FakeStream:
    def __init__(self, message: Message) -> None:
        self._message = message

    async def __aenter__(self) -> "_FakeStream":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def __aiter__(self):
        return
        yield  # pragma: no cover -- makes this an async generator with zero events

    async def get_final_message(self) -> Message:
        return self._message


class _FakeBetaMessages:
    def __init__(self, responses: list[Message], calls: list[dict]) -> None:
        self._responses = list(responses)
        self._calls = calls

    def stream(self, **kwargs: Any) -> _FakeStream:
        self._calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeAnthropicClient ran out of scripted responses")
        message = self._responses.pop(0)
        return _FakeStream(message)


class _FakeBeta:
    def __init__(self, responses: list[Message], calls: list[dict]) -> None:
        self.messages = _FakeBetaMessages(responses, calls)


class FakeAnthropicClient:
    """Drop-in stand-in for anthropic.AsyncAnthropic, scripted with a fixed
    sequence of real anthropic.types.Message responses. Only client.beta.messages.stream(...)
    is implemented -- the only call shape this project's loop ever makes
    (see this plan's Global Constraints on why betas/fallbacks require the
    beta resource)."""

    def __init__(self, responses: list[Message]) -> None:
        self.calls: list[dict] = []
        self.beta = _FakeBeta(responses, self.calls)
