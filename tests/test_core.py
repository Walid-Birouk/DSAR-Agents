"""The agent core on its own: loop, caps, tools, agents as tools, replay, pricing, provider mapping."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import httpx2
import openai
import pytest
from openai.types.chat import ChatCompletion
from pydantic import BaseModel, ConfigDict

from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.agent_tool import AgentTool
from article15.core.events import ListSink
from article15.core.mcp_tools import clean_error
from article15.core.model import ModelRequest, Thinking, ToolCall, Usage
from article15.core.openai_compat import OpenAICompatibleProvider, ProviderConfig
from article15.core.pricing import DEEPSEEK_PEAK, MISTRAL_LIST, cost_usd, deepseek_is_peak, price_for
from article15.core.provider import ProviderError
from article15.core.replay import RecordingProvider, ReplayMiss, ReplayProvider, load_requests
from article15.core.tools import FunctionTool, SubmitTool, Tool, ToolManager, ToolResult, flatten_schema
from tests.fakes import ScriptedProvider, script_of

# --- helpers ------------------------------------------------------------------------------------------


class Echo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int


async def _echo(e: Echo) -> ToolResult:
    return ToolResult(text=f"echo: {e.text}")


def echo_tool() -> Tool:
    return FunctionTool("echo", "Echo the text.", Echo, _echo)


def agent(provider: Any, tools: list[Tool], sink: ListSink | None = None, **kw: Any) -> Agent:
    events = sink if sink is not None else ListSink()
    return Agent(
        name="t", provider=provider, tools=ToolManager(tools, sink=events), model="m", sink=events, **kw
    )


def run(a: Agent, budget: Budget | None = None, messages: list[dict[str, Any]] | None = None) -> Any:
    msgs = (
        messages
        if messages is not None
        else [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    )
    return anyio.run(lambda: a.run(msgs, budget or Budget()))


# --- the loop -----------------------------------------------------------------------------------------


def test_errors_go_back_to_the_model_and_the_run_continues() -> None:
    provider = ScriptedProvider(
        script_of(
            [
                [("nope", {})],
                [("echo", {"text": 1})],
                [("echo", {"text": "hi", "extra": 1})],
                [("submit_result", {"value": 7})],
            ]
        )
    )
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    result = run(agent(provider, [echo_tool(), SubmitTool(Answer)]), messages=msgs)
    assert result.stop_reason is StopReason.COMPLETED and result.final == Answer(value=7)
    tool_texts = [m["content"] for m in msgs if m["role"] == "tool"]
    assert tool_texts[0].startswith("invalid: unknown tool nope")
    assert tool_texts[1].startswith("invalid: text")
    assert tool_texts[2].startswith("invalid:") and "extra" in tool_texts[2]


def test_bad_json_arguments_are_an_error_result() -> None:
    class Raw(ScriptedProvider):
        async def complete(self, request: ModelRequest) -> Any:
            r = await super().complete(request)
            return r.model_copy(update={"tool_calls": [ToolCall(id="x", name="echo", arguments="{not json")]})

    manager = ToolManager([echo_tool()])
    result = anyio.run(lambda: manager.call(ToolCall(id="x", name="echo", arguments="{not json")))
    assert result.is_error and "not valid JSON" in result.text


@pytest.mark.parametrize(
    ("caps", "reason"),
    [
        (Caps(max_calls=3), StopReason.CAP_CALLS),
        (Caps(max_input_tokens=1), StopReason.CAP_INPUT_TOKENS),
        (Caps(max_cost_usd=1e-9), StopReason.CAP_COST),
    ],
)
def test_caps_stop_a_runaway_loop(caps: Caps, reason: StopReason) -> None:
    provider = ScriptedProvider(script_of([[("echo", {"text": "again"})]] * 50))
    result = run(agent(provider, [echo_tool()]), Budget(caps=caps))
    assert result.stop_reason is reason
    assert result.calls <= 3


def test_time_cap_uses_the_budget_clock() -> None:
    now = [0.0]

    def clock() -> float:
        now[0] += 400.0
        return now[0]

    provider = ScriptedProvider(script_of([[("echo", {"text": "x"})]] * 10))
    result = run(agent(provider, [echo_tool()]), Budget(caps=Caps(max_seconds=900), clock=clock))
    assert result.stop_reason is StopReason.CAP_TIME


def test_no_tool_call_is_nudged_then_stops() -> None:
    provider = ScriptedProvider(script_of(["thinking aloud", "still talking", "more talk", "and more"]))
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    result = run(agent(provider, [echo_tool()]), messages=msgs)
    assert result.stop_reason is StopReason.NO_PROGRESS
    assert sum(1 for m in msgs if m["role"] == "user") == 3  # the task and two nudges


def test_calls_after_the_final_one_are_refused() -> None:
    provider = ScriptedProvider(script_of([[("submit_result", {"value": 1}), ("echo", {"text": "late"})]]))
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    result = run(agent(provider, [echo_tool(), SubmitTool(Answer)]), messages=msgs)
    assert result.stop_reason is StopReason.COMPLETED
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 2  # every tool call gets an answer, as the API requires
    assert tool_msgs[1]["content"].startswith("refused:")


def test_reasoning_content_is_kept_for_the_next_request() -> None:
    provider = ScriptedProvider(
        script_of([[("echo", {"text": "a"})], [("submit_result", {"value": 2})]]), reasoning="because"
    )
    run(agent(provider, [echo_tool(), SubmitTool(Answer)], thinking=Thinking.HIGH))
    second = provider.requests[1]
    assistant = [m for m in second.messages if m["role"] == "assistant"]
    assert assistant and assistant[0]["reasoning_content"] == "because"
    assert second.tool_choice == "auto"  # "required" is refused by DeepSeek in thinking mode
    assert provider.requests[0].thinking is Thinking.HIGH


def test_tool_choice_is_required_without_thinking() -> None:
    provider = ScriptedProvider(script_of([[("submit_result", {"value": 2})]]))
    run(agent(provider, [SubmitTool(Answer)]))
    assert provider.requests[0].tool_choice == "required"


def test_provider_errors_stop_cleanly() -> None:
    class Down:
        name = "down"

        async def complete(self, request: ModelRequest) -> Any:
            raise ProviderError("down: timeout", transient=True)

    sink = ListSink()
    result = run(agent(Down(), [echo_tool()], sink=sink))
    assert result.stop_reason is StopReason.PROVIDER_ERROR and "timeout" in (result.error or "")
    assert sink.named("error.occurred")


def test_events_have_scalars_only_and_no_contents() -> None:
    sink = ListSink()
    provider = ScriptedProvider(
        script_of([[("echo", {"text": "SECRET-VALUE"})], [("submit_result", {"value": 1})]])
    )
    run(agent(provider, [echo_tool(), SubmitTool(Answer)], sink=sink))
    names = [e.name for e in sink.events]
    assert names[0] == "agent.start" and names[-1] == "agent.complete"
    assert "SECRET-VALUE" not in repr(sink.events)
    with pytest.raises(TypeError):
        sink.emit("tool.invoke", payload={"x": 1})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        sink.emit("made.up")


# --- tools --------------------------------------------------------------------------------------------


def test_flatten_schema_inlines_refs_and_keeps_a_property_called_title() -> None:
    class Inner(BaseModel):
        title: str

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    flat = flatten_schema(Outer.model_json_schema())
    text = json.dumps(flat)
    assert "$ref" not in text and "$defs" not in text
    assert flat["properties"]["inner"]["properties"]["title"] == {"type": "string"}
    assert "title" not in flat


def test_guards_refuse_before_the_tool_runs() -> None:
    ran = []

    async def body(e: Echo) -> ToolResult:
        ran.append(e)
        return ToolResult(text="ran")

    manager = ToolManager(
        [FunctionTool("echo", "e", Echo, body)],
        guards=[lambda name, args: "R-TEST: not today" if args.get("text") == "no" else None],
    )
    refused = anyio.run(lambda: manager.call(ToolCall(id="1", name="echo", arguments='{"text": "no"}')))
    allowed = anyio.run(lambda: manager.call(ToolCall(id="2", name="echo", arguments='{"text": "yes"}')))
    assert refused.text == "refused: R-TEST: not today" and refused.error_kind == "refused"
    assert allowed.text == "ran" and len(ran) == 1
    assert [e.ok for e in manager.log] == [False, True]


def test_tool_timeouts_and_crashes_become_results() -> None:
    async def slow(e: Echo) -> ToolResult:
        await anyio.sleep(5)
        return ToolResult(text="late")

    async def crash(e: Echo) -> ToolResult:
        raise RuntimeError("boom")

    manager = ToolManager(
        [FunctionTool("slow", "s", Echo, slow, timeout_s=0.05), FunctionTool("crash", "c", Echo, crash)]
    )
    t = anyio.run(lambda: manager.call(ToolCall(id="1", name="slow", arguments='{"text": "x"}')))
    c = anyio.run(lambda: manager.call(ToolCall(id="2", name="crash", arguments='{"text": "x"}')))
    assert t.text.startswith("timeout:") and c.text.startswith("error: crash failed (RuntimeError")


def test_duplicate_tool_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ToolManager([echo_tool(), echo_tool()])


def test_mcp_errors_are_cleaned_and_tokens_scrubbed() -> None:
    raw = (
        "Error executing tool crm_read_record: 2 validation errors for crm_read_recordArguments\n"
        "table\n  Field "
        "required [type=missing, input_value={'case_token': 'a15g.3729...cfeea05c55'}, input_type=dict]\n    "
        "For further information visit https://errors.pydantic.dev/2.14/v/missing"
    )
    cleaned = clean_error(raw)
    assert "a15g" not in cleaned and "Error executing" not in cleaned and "pydantic.dev" not in cleaned
    assert cleaned.startswith("invalid:")
    assert clean_error("Error executing tool x: refused: not yours") == "refused: not yours"


# --- agents as tools ----------------------------------------------------------------------------------


def test_an_agent_runs_as_a_tool_and_charges_its_caller() -> None:
    parent = Budget(caps=Caps(max_calls=10))
    specialist = ScriptedProvider(script_of([[("submit_result", {"value": 42})]]))
    tool = AgentTool(
        "ask_specialist",
        "Ask the specialist.",
        Echo,
        make_agent=lambda: agent(specialist, [SubmitTool(Answer)]),
        system_prompt="You are a specialist.",
        render_input=lambda e: f"Task: {e.text}",
        parent_budget=parent,
        caps=Caps(max_calls=2),
    )
    result = anyio.run(lambda: tool.run({"text": "the question"}))
    assert result.data == Answer(value=42) and json.loads(result.text) == {"value": 42}
    assert parent.calls == 1
    first = specialist.requests[0].messages
    assert first[0]["content"] == "You are a specialist." and first[1]["content"] == "Task: the question"


def test_a_failed_specialist_returns_an_error_not_half_an_answer() -> None:
    looping = ScriptedProvider(script_of([[("echo", {"text": "x"})]] * 10))
    tool = AgentTool(
        "ask",
        "a",
        Echo,
        make_agent=lambda: agent(looping, [echo_tool(), SubmitTool(Answer)]),
        system_prompt="s",
        render_input=lambda e: e.text,
        parent_budget=Budget(),
        caps=Caps(max_calls=2),
    )
    result = anyio.run(lambda: tool.run({"text": "q"}))
    assert result.is_error and "cap_calls" in result.text


# --- replay -------------------------------------------------------------------------------------------


def _record(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    inner = ScriptedProvider(script_of([[("echo", {"text": "a"})], [("submit_result", {"value": 3})]]))
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    result = run(agent(RecordingProvider(inner, path, "t"), [echo_tool(), SubmitTool(Answer)]), messages=msgs)
    return result, msgs


def test_record_and_replay_give_the_same_run(tmp_path: Path) -> None:
    cassette = tmp_path / "c.jsonl"
    live, live_msgs = _record(cassette)
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    replayer = ReplayProvider(cassette)
    again = run(agent(replayer, [echo_tool(), SubmitTool(Answer)]), messages=msgs)
    assert again.final == live.final and msgs == live_msgs and replayer.unused() == 0
    lines = [json.loads(line) for line in cassette.read_text(encoding="utf-8").splitlines()]
    calls = [line for line in lines if line["kind"] == "call"]
    assert calls[1]["prefix"] == 2 and len(calls[1]["messages"]) == 2  # only what was added is stored
    assert [r.digest() for r, _ in load_requests(cassette)] == [c["digest"] for c in calls]


def test_replay_names_the_first_difference(tmp_path: Path) -> None:
    cassette = tmp_path / "c.jsonl"
    _record(cassette)
    msgs: list[dict[str, Any]] = [{"role": "system", "content": "changed"}, {"role": "user", "content": "u"}]
    replayer = ReplayProvider(cassette)
    request = ModelRequest(model="m", messages=msgs, tools=[], tool_choice="required", max_tokens=8192)
    with pytest.raises(ReplayMiss, match=r"tool definitions differ|message 0 differs"):
        anyio.run(lambda: replayer.complete(request))


def test_digest_is_canonical() -> None:
    a = ModelRequest(model="m", messages=[{"role": "user", "content": "é", "x": 1}])
    b = ModelRequest(model="m", messages=[{"x": 1, "content": "é", "role": "user"}])
    assert a.digest() == b.digest()
    assert a.digest() != ModelRequest(model="m2", messages=a.messages).digest()


# --- pricing ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "peak"),
    [
        (datetime(2026, 11, 23, 2, 30, tzinfo=UTC), True),  # Monday, 01:00-04:00 UTC
        (datetime(2026, 11, 23, 4, 0, tzinfo=UTC), False),  # the window ends at 04:00
        (datetime(2026, 11, 23, 9, 59, tzinfo=UTC), True),  # 06:00-10:00 UTC
        (datetime(2026, 11, 23, 12, 0, tzinfo=UTC), False),
        (datetime(2026, 11, 21, 2, 30, tzinfo=UTC), False),  # Saturday
        (datetime(2026, 10, 1, 2, 30, tzinfo=UTC), False),  # Thursday, Chinese National Day
    ],
)
def test_deepseek_peak_hours(at: datetime, peak: bool) -> None:
    assert deepseek_is_peak(at) is peak


def test_off_peak_is_half_price_and_mistral_eu_costs_more() -> None:
    peak = price_for("deepseek", "deepseek-flash", datetime(2026, 11, 23, 2, tzinfo=UTC))
    off = price_for("deepseek", "deepseek-flash", datetime(2026, 11, 23, 12, tzinfo=UTC))
    assert peak == DEEPSEEK_PEAK["deepseek-flash"] and off.cache_miss == pytest.approx(peak.cache_miss / 2)
    eu = price_for("mistral", "mistral-small-2603", datetime(2026, 11, 23, tzinfo=UTC), eu_endpoint=True)
    assert eu.output == pytest.approx(MISTRAL_LIST["mistral-small-2603"].output * 1.1)
    usage = Usage(
        input_tokens=1_000_000, cache_hit_tokens=600_000, cache_miss_tokens=400_000, output_tokens=0
    )
    assert cost_usd(usage, peak) == pytest.approx(0.6 * 0.006 + 0.4 * 0.30)


# --- the OpenAI-compatible provider -------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.bodies: list[dict[str, Any]] = []

    async def create(self, **body: Any) -> Any:
        self.bodies.append(body)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class _FakeClient:
    def __init__(self, reply: Any) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(reply)})()


def _deepseek_completion() -> ChatCompletion:
    return ChatCompletion.model_validate(
        {
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "reasoning_content": "thought",
                        "tool_calls": [
                            {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}
                        ],
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 50,
                "total_tokens": 1050,
                "prompt_cache_hit_tokens": 800,
                "prompt_cache_miss_tokens": 200,
                "completion_tokens_details": {"reasoning_tokens": 30},
            },
        }
    )


def test_deepseek_response_mapping_and_thinking_switch() -> None:
    client = _FakeClient(_deepseek_completion())
    provider = OpenAICompatibleProvider(
        ProviderConfig(name="deepseek", base_url="https://api.deepseek.com", api_key="k"),
        client=client,
        now=lambda: datetime(2026, 11, 23, 12, tzinfo=UTC),
    )
    off = ModelRequest(model="deepseek-flash", messages=[{"role": "user", "content": "u"}], tools=[{"x": 1}])
    response = anyio.run(lambda: provider.complete(off))
    assert response.usage == Usage(
        input_tokens=1000, cache_hit_tokens=800, cache_miss_tokens=200, output_tokens=50, reasoning_tokens=30
    )
    assert response.reasoning_content == "thought" and response.tool_calls[0].name == "echo"
    assert response.cost_usd == pytest.approx((800 * 0.003 + 200 * 0.15 + 50 * 0.6) / 1e6)
    bodies = client.chat.completions.bodies
    assert (
        bodies[0]["extra_body"] == {"thinking": {"type": "disabled"}} and "reasoning_effort" not in bodies[0]
    )
    on = off.model_copy(update={"thinking": Thinking.LOW})
    anyio.run(lambda: provider.complete(on))
    assert (
        bodies[1]["extra_body"] == {"thinking": {"type": "enabled"}}
        and bodies[1]["reasoning_effort"] == "low"
    )


def test_provider_errors_are_classified() -> None:
    request = httpx2.Request("POST", "https://api.deepseek.com/chat/completions")
    limited = openai.RateLimitError("slow down", response=httpx2.Response(429, request=request), body=None)
    bad = openai.BadRequestError("bad", response=httpx2.Response(400, request=request), body=None)
    for error, transient in ((limited, True), (bad, False)):
        provider = OpenAICompatibleProvider(
            ProviderConfig(name="deepseek", base_url="https://api.deepseek.com", api_key="k"),
            client=_FakeClient(error),
        )
        with pytest.raises(ProviderError) as info:
            anyio.run(lambda p=provider: p.complete(ModelRequest(model="deepseek-flash", messages=[])))  # type: ignore[misc]
        assert info.value.transient is transient
