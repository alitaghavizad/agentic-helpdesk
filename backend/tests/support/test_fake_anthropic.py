from __future__ import annotations

from anthropic.types import ToolUseBlock

from tests.support.fake_anthropic import FakeAnthropicClient, make_text_message, make_tool_use_message


async def test_fake_client_streams_scripted_messages_in_order():
    msg1 = make_tool_use_message(tool_name="search_knowledge", tool_input={"query": "vpn"}, tool_use_id="t1")
    msg2 = make_text_message(text="Here's what I found.")
    client = FakeAnthropicClient([msg1, msg2])

    async with client.beta.messages.stream(model="claude-opus-5", max_tokens=1024, messages=[]) as stream:
        async for _event in stream:
            pass
        first = await stream.get_final_message()
    async with client.beta.messages.stream(model="claude-opus-5", max_tokens=1024, messages=[]) as stream:
        async for _event in stream:
            pass
        second = await stream.get_final_message()

    assert first is msg1
    assert second is msg2
    assert isinstance(first.content[0], ToolUseBlock)
    assert second.content[0].text == "Here's what I found."


async def test_fake_client_records_call_kwargs():
    client = FakeAnthropicClient([make_text_message(text="ok")])
    async with client.beta.messages.stream(
        model="claude-opus-5", max_tokens=999, messages=[{"role": "user", "content": "hi"}],
        thinking={"type": "adaptive", "display": "summarized"},
    ) as stream:
        async for _event in stream:
            pass
        await stream.get_final_message()

    assert len(client.calls) == 1
    assert client.calls[0]["max_tokens"] == 999
    assert client.calls[0]["thinking"] == {"type": "adaptive", "display": "summarized"}


def test_make_tool_use_message_usage_defaults_are_present():
    msg = make_tool_use_message(tool_name="get_my_profile", tool_input={}, tool_use_id="t2")
    assert msg.usage.input_tokens >= 0
    assert msg.usage.cache_read_input_tokens is not None
    assert msg.stop_reason == "tool_use"


def test_make_text_message_defaults_to_end_turn():
    msg = make_text_message(text="done")
    assert msg.stop_reason == "end_turn"
