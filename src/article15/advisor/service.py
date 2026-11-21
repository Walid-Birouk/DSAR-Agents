"""The DPO advisor: a separate service reached over A2A (PLAN.md 5.4).

It has its own judgment and its own GDPR knowledge, and it only ever sees a minimised case summary. Two ways
to
advise, one protocol:
- `rules_advice`: deterministic advice from the passages in knowledge.py. It needs no model, so offline runs,
  replays and the demo's recorded mode always have an advisor.
- `ModelAdvisor`: an agent on its own model, with a knowledge tool and a typed answer.

`build_app` serves either one as an A2A agent: the Agent Card at the well-known path (public), JSON-RPC at
/a2a behind a bearer key. `AdvisorClient` is the desk's side: it runs the egress filter, sends the summary,
and returns the typed advice.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers.proto_helpers import get_message_text, new_text_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Role,
    SendMessageRequest,
)
from pydantic import BaseModel, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from article15.advisor.egress import outgoing
from article15.advisor.knowledge import passages_for
from article15.advisor.models import Advice, CaseSummary, Question, Recommendation
from article15.core.agent import Agent, Budget, Caps, StopReason
from article15.core.provider import Provider
from article15.core.tools import FunctionTool, SubmitTool, ToolManager, ToolResult
from article15.schemas import RequestType, Strict

RPC_PATH = "/a2a"
REPEAT_THRESHOLD = 2  # earlier requests in 12 months from which repetition may be a ground (read narrowly)
EXTENSION_RECORDS = 150  # records from which a request may be "complex" (a person still decides)
Advise = Callable[[CaseSummary], Awaitable[Advice]]


# --- the advice
# ----------------------------------------------------------------------------------------------


async def rules_advice(s: CaseSummary) -> Advice:
    sources = [p.source for p in passages_for(s.question)]
    if s.question is Question.EXCESSIVE:
        if s.earlier_requests_last_12_months >= REPEAT_THRESHOLD:
            return Advice(
                recommendation=Recommendation.PERSON_DECIDES,
                reasons=[
                    f"{s.earlier_requests_last_12_months} earlier requests in the last 12 months: repetition "
                    "may make the request excessive, but the concept is read narrowly and the controller "
                    "bears "
                    "the burden of proof; a person decides and records the reasons",
                ],
                sources=sources,
            )
        return Advice(
            recommendation=Recommendation.PROCEED,
            reasons=["no repetition that could make the request excessive; it is answered as usual"],
            sources=sources,
        )
    if s.question is Question.EXTENSION:
        if s.records_found >= EXTENSION_RECORDS or (s.systems_with_records == 4 and s.records_found >= 100):
            return Advice(
                recommendation=Recommendation.EXTENSION_JUSTIFIED,
                reasons=[
                    f"{s.records_found} records across {s.systems_with_records} systems may make the request "
                    "complex; an extension of up to two months is possible if the requester is told within "
                    "the "
                    "first month, with the reasons",
                ],
                sources=sources,
            )
        return Advice(
            recommendation=Recommendation.PROCEED,
            reasons=[f"{s.records_found} records can be answered within the month"],
            sources=sources,
        )
    if s.question is Question.SPECIAL_CATEGORY:
        return Advice(
            recommendation=Recommendation.FLAG_FOR_REVIEW,
            reasons=[
                f"{s.special_category_mentions} special-category mention(s) about the requester: they stay "
                f"in "
                "the copy, and the reviewer acknowledges them before release",
            ],
            sources=sources,
        )
    others = [t.value for t in s.request_types if t is not RequestType.ACCESS]
    return Advice(
        recommendation=Recommendation.PERSON_DECIDES,
        reasons=[f"the request also asks for {', '.join(others) or 'another right'}; a person handles it"],
        sources=sources,
    )


class Lookup(Strict):
    question: Question


class ModelAdvisor:
    """The advisor as an agent on its own model: it looks up passages, then returns typed advice."""

    def __init__(self, provider: Provider, model: str, caps: Caps | None = None) -> None:
        self.provider = provider
        self.model = model
        self.caps = caps or Caps(max_calls=4, max_input_tokens=40_000, max_seconds=60, max_cost_usd=0.02)

    async def __call__(self, s: CaseSummary) -> Advice:
        async def lookup(q: Lookup) -> ToolResult:
            return ToolResult(text="\n".join(f"[{p.source}] {p.text}" for p in passages_for(q.question)))

        tools = ToolManager(
            [
                FunctionTool("gdpr_lookup", "The GDPR passages relevant to a question.", Lookup, lookup),
                SubmitTool(Advice, description="Return your advice, citing the passages you relied on."),
            ]
        )
        agent = Agent(name="advisor", provider=self.provider, tools=tools, model=self.model, max_tokens=800)
        system = (
            "You are a data-protection advisor. You receive a minimised summary of an access request (GDPR "
            "Article 15) and a question. Look up the relevant passages with gdpr_lookup, then give advice: "
            "proceed, person_decides, extension_justified or flag_for_review, with short reasons and the "
            "sources "
            "you relied on. Read 'manifestly unfounded or excessive' narrowly. You advise; a person decides."
        )
        messages: list[Any] = [
            {"role": "system", "content": system},
            {"role": "user", "content": s.model_dump_json()},
        ]
        run = await agent.run(messages, Budget(caps=self.caps))
        if run.stop_reason is not StopReason.COMPLETED or not isinstance(run.final, Advice):
            return await rules_advice(s)
        return run.final.model_copy(update={"advisor": self.model})


# --- the A2A server
# --------------------------------------------------------------------------------------------


def agent_card(base_url: str) -> AgentCard:
    skills = [
        AgentSkill(
            id=q.value,
            name=q.value.replace("_", " "),
            description=f"Advice on a {q.value.replace('_', ' ')} question about an access request, from a "
            "minimised case summary (no personal data).",
            tags=["gdpr", "article-15", "advice"],
        )
        for q in Question
    ]
    return AgentCard(
        name="Ferlane DPO advisor",
        description="Advises the access-request desk on edge cases. It never receives personal data.",
        version="1",
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC", url=base_url.rstrip("/") + RPC_PATH, protocol_version="1.0"
            )
        ],
        capabilities=AgentCapabilities(streaming=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )


class _Executor(AgentExecutor):
    def __init__(self, advise: Advise) -> None:
        self.advise = advise

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        try:
            summary = CaseSummary.model_validate_json(context.get_user_input())
        except ValidationError as exc:
            reply = json.dumps({"error": f"invalid case summary: {exc.error_count()} error(s)"})
        else:
            reply = (await self.advise(summary)).model_dump_json()
        await event_queue.enqueue_event(new_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        return None


class _BearerKey(BaseHTTPMiddleware):
    def __init__(self, app: Any, key: str) -> None:
        super().__init__(app)
        self.key = key

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path.startswith(RPC_PATH):
            given = request.headers.get("authorization", "")
            if not hmac.compare_digest(given.encode(), f"Bearer {self.key}".encode()):
                return JSONResponse({"error": "a valid bearer key is required"}, status_code=401)
        return await call_next(request)


def build_app(advise: Advise, api_key: str, base_url: str = "http://advisor.internal") -> Starlette:
    if len(api_key) < 16:
        raise ValueError("the advisor's bearer key must be at least 16 characters")
    card = agent_card(base_url)
    handler = DefaultRequestHandler(
        agent_executor=_Executor(advise), task_store=InMemoryTaskStore(), agent_card=card
    )
    app = Starlette(routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, RPC_PATH)])
    app.add_middleware(_BearerKey, key=api_key)
    return app


# --- the desk's client
# -----------------------------------------------------------------------------------------


class AdvisorError(Exception):
    pass


class PayloadLog(BaseModel):
    """Every payload that left for the advisor, for the eval's egress gate."""

    path: Path | None = None
    sent: list[str] = Field(default_factory=list)

    def add(self, text: str) -> None:
        self.sent.append(text)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(text + "\n")


class AdvisorClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
        log: PayloadLog | None = None,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.transport = transport
        self.timeout_s = timeout_s
        self.log = log or PayloadLog()

    async def consult(self, summary: CaseSummary, never: Iterable[str] = ()) -> Advice:
        payload = outgoing(summary, never)  # raises EgressRefused: nothing is sent
        headers = {"authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(
            transport=self.transport, base_url=self.base_url, headers=headers, timeout=self.timeout_s
        ) as http:
            try:
                client = await create_client(self.base_url, ClientConfig(httpx_client=http, streaming=False))
                self.log.add(payload)
                request = SendMessageRequest(message=new_text_message(payload, role=Role.ROLE_USER))
                text = ""
                async for response in client.send_message(request):
                    if response.HasField("message"):
                        text = get_message_text(response.message)
            except (httpx.HTTPError, OSError) as exc:
                raise AdvisorError(f"the advisor cannot be reached ({type(exc).__name__})") from exc
            except Exception as exc:  # the SDK's own errors (bad status, protocol)
                raise AdvisorError(f"the advisor call failed ({type(exc).__name__}: {exc})") from exc
        try:
            return Advice.model_validate_json(text)
        except ValidationError as exc:
            raise AdvisorError(
                f"the advisor's answer is not valid advice ({exc.error_count()} error(s))"
            ) from exc
