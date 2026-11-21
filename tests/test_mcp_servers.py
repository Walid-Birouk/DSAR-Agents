"""Contract tests: each server, spoken to over MCP (in memory), behaves as the agents will rely on."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.client import Client
from mcp.server import MCPServer

from article15.company.models import World
from article15.company.store import DataPaths
from article15.mcp_servers.common import SearchResult, ServerContext, build_server, run_tool
from article15.mcp_servers.registry import SERVER_NAMES, build_named
from article15.scope.authority import ScopeResolver
from article15.scope.store import ScopeStore
from tests.conftest import SECRET, Desk, subject_token

EXPECTED_TOOLS = {
    "crm": {"crm_check_identity", "crm_get_customer", "crm_read_record"},
    "orders": {"orders_check_reference", "orders_search", "orders_read_record"},
    "helpdesk": {"helpdesk_search_tickets", "helpdesk_read_ticket"},
    "weblogs": {"weblogs_search_events", "weblogs_read_record"},
    "notifier": {"notify_post_message", "notify_read_replies"},
}


def server(desk: Desk, name: str) -> MCPServer:
    return build_named(name, desk.ctx, desk.notifier)


def list_tools(srv: MCPServer) -> list[Any]:
    async def go() -> list[Any]:
        async with Client(srv) as client:
            return list((await client.list_tools()).tools)

    return anyio.run(go)


def call(srv: MCPServer, tool: str, args: dict[str, Any]) -> Any:
    async def go() -> Any:
        async with Client(srv) as client:
            return await client.call_tool(tool, args)

    return anyio.run(go)


def text(result: Any) -> str:
    return "".join(getattr(c, "text", "") for c in result.content)


@pytest.mark.parametrize("name", SERVER_NAMES)
def test_tools_are_named_after_their_server_and_strict(desk: Desk, name: str) -> None:
    tools = list_tools(server(desk, name))
    assert {t.name for t in tools} == EXPECTED_TOOLS[name]
    prefix = "notify" if name == "notifier" else name
    for t in tools:
        assert t.name.startswith(f"{prefix}_")
        assert t.input_schema["additionalProperties"] is False
        assert "case_token" in t.input_schema["required"]
        assert t.output_schema, t.name
        assert t.description
        assert t.annotations.read_only_hint is (name != "notifier")


@pytest.mark.parametrize("name", SERVER_NAMES)
def test_unknown_arguments_are_rejected_not_dropped(desk: Desk, world: World, name: str) -> None:
    token = subject_token(desk, world, world.customers[0].customer_id)
    tool = sorted(EXPECTED_TOOLS[name])[0]
    result = call(server(desk, name), tool, {"case_token": token, "customer_id": "C0002"})
    assert result.is_error
    assert "invalid: unknown argument(s) customer_id" in text(result)


def test_missing_arguments_are_rejected(desk: Desk) -> None:
    result = call(server(desk, "crm"), "crm_get_customer", {})
    assert result.is_error and "case_token" in text(result)


def test_out_of_scope_calls_return_one_readable_line(desk: Desk, world: World) -> None:
    a, b = world.customers[0], world.customers[1]
    token = subject_token(desk, world, a.customer_id)
    result = call(
        server(desk, "crm"),
        "crm_read_record",
        {"case_token": token, "table": "customers", "record_id": b.customer_id},
    )
    assert result.is_error
    assert "refused: crm record customers/" in text(result)
    result = call(server(desk, "crm"), "crm_get_customer", {"case_token": "forged"})
    assert result.is_error and "refused: invalid case token" in text(result)


def test_structured_results_match_the_declared_schema(desk: Desk, world: World) -> None:
    cid = world.customers[0].customer_id
    token = subject_token(desk, world, cid)
    result = call(server(desk, "crm"), "crm_get_customer", {"case_token": token})
    assert not result.is_error
    parsed = SearchResult.model_validate(result.structured_content)
    assert any(r.table == "customers" and r.record_id == cid for r in parsed.records)
    assert all("@" not in r.summary for r in parsed.records)  # summaries hold no contact details


def test_identity_check_works_before_verification(desk: Desk, world: World) -> None:
    c = world.customers[3]
    identity = desk.authority.open_case("CASE-MCP", c.email)
    result = call(
        server(desk, "crm"), "crm_check_identity", {"case_token": identity, "claimed_email": c.email}
    )
    assert result.structured_content["sender_customer_id"] == c.customer_id
    assert result.structured_content["claimed_email_registered"] is True
    result = call(server(desk, "orders"), "orders_search", {"case_token": identity})
    assert result.is_error and "not verified" in text(result)


def test_order_reference_check_reveals_the_customer_only_with_the_right_postcode(
    desk: Desk, world: World
) -> None:
    order = next(o for o in world.orders if o.customer_id)
    identity = desk.authority.open_case("CASE-REF", "someone@courriel.example")
    srv = server(desk, "orders")
    right = call(
        srv,
        "orders_check_reference",
        {"case_token": identity, "order_reference": order.order_id, "postcode": order.ship_postcode},
    )
    wrong = call(
        srv,
        "orders_check_reference",
        {"case_token": identity, "order_reference": order.order_id, "postcode": "00000"},
    )
    assert right.structured_content["customer_id"] == order.customer_id
    assert wrong.structured_content == {"order_found": True, "postcode_matches": False, "customer_id": None}


def test_a_slow_system_times_out_with_a_readable_error(desk: Desk) -> None:
    slow_ctx = ServerContext(paths=desk.ctx.paths, resolver=desk.ctx.resolver, tool_timeout_s=0.05)

    async def crm_slow(case_token: str) -> SearchResult:
        """Sleeps."""
        return await run_tool(
            slow_ctx, "crm", lambda: (time.sleep(0.5), SearchResult(records=[], total=0))[1]
        )

    result = call(build_server("slow", "", [crm_slow], read_only=True), "crm_slow", {"case_token": "x"})
    assert result.is_error and "timeout: crm did not answer within 0.05 s" in text(result)


def test_a_missing_system_is_reported_unavailable(desk: Desk, world: World, tmp_path: Path) -> None:
    token = subject_token(desk, world, world.customers[0].customer_id)
    broken = ServerContext(
        paths=DataPaths(tmp_path / "empty"),
        resolver=ScopeResolver(ScopeStore(desk.authority.store.path, read_only=True), SECRET, desk.clock),
    )
    result = call(build_named("helpdesk", broken), "helpdesk_search_tickets", {"case_token": token})
    assert result.is_error and "unavailable: helpdesk cannot be reached" in text(result)


def test_notifier_writes_to_the_requester_and_reads_only_their_replies(desk: Desk, world: World) -> None:
    truth = next(t for t in world.truths if t.links_needing_proof and t.subject_id)
    assert truth.subject_id
    sender = next(c.email for c in world.customers if c.customer_id == truth.subject_id)
    token = subject_token(desk, world, truth.subject_id, "CASE-NOTE")
    srv = server(desk, "notifier")
    posted = call(
        srv, "notify_post_message", {"case_token": token, "subject": "Question", "body": "Is this you?"}
    )
    assert not posted.is_error
    assert [n.address for n in desk.notifier.sent("CASE-NOTE")] == [sender]
    # A proof-of-control code goes to the other mailbox; the agent's tools must not see it.
    address = truth.links_needing_proof[0]
    ticket = next(
        t.record_id
        for t in desk.helpdesk.search_tickets(token, None).records
        if t.table == "tickets"
        and any(address in m.body.lower() for m in desk.helpdesk.read_ticket(token, t.record_id).messages)
    )
    desk.authority.start_link(token, address, ticket)
    desk.notifier.receive("CASE-NOTE", sender, "Yes, that's me.", desk.clock())
    replies = call(srv, "notify_read_replies", {"case_token": token}).structured_content["replies"]
    assert [r["body"] for r in replies] == ["Yes, that's me."]
    code_mail = desk.notifier.mailbox(address)[-1].body
    assert all(code_mail not in r["body"] for r in replies)


def test_notifier_refuses_empty_or_oversized_messages(desk: Desk, world: World) -> None:
    token = subject_token(desk, world, world.customers[0].customer_id)
    srv = server(desk, "notifier")
    empty = call(srv, "notify_post_message", {"case_token": token, "subject": " ", "body": "x"})
    huge = call(srv, "notify_post_message", {"case_token": token, "subject": "s", "body": "x" * 10_001})
    assert empty.is_error and "invalid:" in text(empty)
    assert huge.is_error
