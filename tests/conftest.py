"""Shared fixtures: one generated world per test session, and a fresh desk (scope + notifier) per test."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from article15.company.generator import generate
from article15.company.models import CaseTruth, RecordKey, World
from article15.company.store import DataPaths, write_world
from article15.mcp_servers.common import ServerContext
from article15.mcp_servers.crm import CrmService
from article15.mcp_servers.helpdesk import HelpdeskService
from article15.mcp_servers.orders import OrdersService
from article15.mcp_servers.weblogs import WeblogsService
from article15.notifier.store import NotifierStore
from article15.scope.authority import ScopeAuthority, ScopeResolver
from article15.scope.store import ScopeStore

SECRET = bytes(range(32))
START = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class Desk:
    data: DataPaths
    authority: ScopeAuthority
    notifier: NotifierStore
    ctx: ServerContext
    clock: Clock
    crm: CrmService
    orders: OrdersService
    helpdesk: HelpdeskService
    weblogs: WeblogsService


def make_desk(data: DataPaths, runtime: Path) -> Desk:
    clock = Clock(START)
    store = ScopeStore(runtime / "scope.sqlite")
    notifier = NotifierStore(runtime / "notifier.sqlite")
    authority = ScopeAuthority(data, store, notifier, SECRET, clock)
    ctx = ServerContext(
        paths=data, resolver=ScopeResolver(ScopeStore(store.path, read_only=True), SECRET, clock)
    )
    return Desk(
        data=data,
        authority=authority,
        notifier=notifier,
        ctx=ctx,
        clock=clock,
        crm=CrmService(ctx),
        orders=OrdersService(ctx),
        helpdesk=HelpdeskService(ctx),
        weblogs=WeblogsService(ctx),
    )


@pytest.fixture(scope="session")
def world() -> World:
    return generate()


@pytest.fixture(scope="session")
def data(world: World, tmp_path_factory: pytest.TempPathFactory) -> DataPaths:
    return write_world(world, tmp_path_factory.mktemp("generated"))


@pytest.fixture
def desk(data: DataPaths, tmp_path: Path) -> Iterator[Desk]:
    yield make_desk(data, tmp_path)


def customer_email(world: World, customer_id: str) -> str:
    return next(c.email for c in world.customers if c.customer_id == customer_id)


def subject_token(desk: Desk, world: World, customer_id: str, case_id: str = "TEST-1") -> str:
    """A subject grant obtained the ordinary way: a request from the registered address."""
    email = customer_email(world, customer_id)
    identity = desk.authority.open_case(case_id, email)
    return desk.authority.grant_subject(identity, customer_id, request_text="")


def discover(desk: Desk, token: str) -> set[RecordKey]:
    """Everything the four systems return for a token: what a perfect discovery agent could find."""
    found = {
        RecordKey(system=r.system, table=r.table, record_id=r.record_id)
        for r in desk.crm.get_customer(token).records
    }
    found |= {
        RecordKey(system=r.system, table=r.table, record_id=r.record_id)
        for r in desk.orders.search(token).records
    }
    found |= {
        RecordKey(system=r.system, table=r.table, record_id=r.record_id)
        for r in desk.helpdesk.search_tickets(token, None).records
    }
    offset = 0
    while True:
        page = desk.weblogs.search_events(token, offset, 500)
        found |= {RecordKey(system=r.system, table=r.table, record_id=r.record_id) for r in page.records}
        offset += len(page.records)
        if not page.truncated:
            return found


def prove_links(desk: Desk, token: str, truth: CaseTruth) -> None:
    """Play the requester: link every address the truth says needs proof, reading codes from their mailbox."""
    for address in truth.links_needing_proof:
        assert address in truth.requester.mailboxes
        evidence = next(
            t.record_id
            for t in desk.helpdesk.search_tickets(token, None).records
            if t.table == "tickets"
            and any(
                address in m.body.lower() and m.author == "customer"
                for m in desk.helpdesk.read_ticket(token, t.record_id).messages
            )
        )
        desk.authority.start_link(token, address, evidence)
        code = latest_code(desk, address)
        desk.authority.complete_link(token, address, code)


def latest_code(desk: Desk, address: str) -> str:
    import re

    mail = [n for n in desk.notifier.mailbox(address) if n.kind == "link_code"][-1]
    match = re.search(r"\b(\d{6})\b", mail.body)
    assert match
    return match.group(1)
