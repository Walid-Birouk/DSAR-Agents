"""Subject scope: person A's token never reads person B, and widening the scope needs proof of control.

The oracle is the generator's owner map (who each record is really about), which no system or tool can read.
The last tests break the servers on purpose to show these checks can fail.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Callable
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import pytest

from article15.company.models import CaseKind, RecordKey, Resolution, World
from article15.company.store import DataPaths
from article15.mcp_servers.helpdesk import HelpdeskService
from article15.schemas import System
from article15.scope import authority as authority_module
from article15.scope import tokens
from article15.scope.authority import ScopeRefused, keyed_link_code, random_link_code
from tests.conftest import (
    SECRET,
    Desk,
    customer_email,
    discover,
    latest_code,
    make_desk,
    prove_links,
    subject_token,
)


def owners(world: World) -> dict[tuple[str, str, str], str]:
    return {o.record.sort_key(): o.person_id for o in world.owners}


def foreign(found: set[RecordKey], world: World, subject: str) -> set[RecordKey]:
    who = owners(world)
    return {k for k in found if who[k.sort_key()] != subject}


def released_cases(world: World) -> list[tuple[str, str]]:
    """(case ID, subject ID) of every case whose truth lists records for a pack."""
    return [(t.case_id, t.subject_id) for t in world.truths if t.records and t.subject_id]


# --- tokens and grants ----------------------------------------------------------------------------------


def test_tokens_carry_no_personal_data_and_cannot_be_forged(desk: Desk, world: World) -> None:
    cid = world.customers[0].customer_id
    token = subject_token(desk, world, cid)
    assert cid not in token and customer_email(world, cid) not in token
    prefix, grant_id, sig = token.split(".")
    for forged in (
        f"{prefix}.{grant_id}.{'0' * len(sig)}",
        f"{prefix}.{'1' * 32}.{sig}",
        "a15g..",
        "garbage",
    ):
        with pytest.raises(ScopeRefused, match="invalid case token"):
            desk.crm.get_customer(forged)
    unknown = tokens.issue(SECRET, tokens.new_grant_id())
    with pytest.raises(ScopeRefused, match="unknown case token"):
        desk.crm.get_customer(unknown)


def test_tokens_expire_and_can_be_revoked(desk: Desk, world: World) -> None:
    cid = world.customers[1].customer_id
    token = subject_token(desk, world, cid, "CASE-EXP")
    desk.crm.get_customer(token)
    desk.clock.advance(timedelta(hours=24))
    with pytest.raises(ScopeRefused, match="expired"):
        desk.crm.get_customer(token)
    fresh = subject_token(desk, world, cid, "CASE-REV")
    desk.authority.revoke_case("CASE-REV")
    with pytest.raises(ScopeRefused, match="revoked"):
        desk.crm.get_customer(fresh)


def test_identity_stage_token_reads_no_records(desk: Desk, world: World) -> None:
    c = world.customers[2]
    identity = desk.authority.open_case("CASE-ID", c.email)
    assert desk.crm.check_identity(identity, None).sender_customer_id == c.customer_id
    for read in (
        lambda: desk.crm.get_customer(identity),
        lambda: desk.orders.search(identity),
        lambda: desk.helpdesk.search_tickets(identity, None),
        lambda: desk.weblogs.search_events(identity, 0, 10),
    ):
        with pytest.raises(ScopeRefused, match="not verified"):
            read()


# --- identity evidence (scope.identity_evidence, R-IDENTITY) -------------------------------------------


def _customer_with_order(world: World) -> tuple[str, str, str]:
    order = next(o for o in world.orders if o.customer_id)
    assert order.customer_id
    return order.customer_id, order.order_id, order.ship_postcode


def test_grant_from_unregistered_address_needs_order_and_postcode(desk: Desk, world: World) -> None:
    cid, order_id, postcode = _customer_with_order(world)
    identity = desk.authority.open_case("CASE-NEW", "new.address@courriel.example")
    with pytest.raises(ScopeRefused) as refused:
        desk.authority.grant_subject(identity, cid, "Please send my data.")
    assert refused.value.guard == "scope.identity_evidence" and refused.value.rule_id == "R-IDENTITY"
    text = f"My last order is {order_id} and my postcode is {postcode}."
    token = desk.authority.grant_subject(identity, cid, text, order_id, postcode)
    assert desk.crm.get_customer(token).records


def test_evidence_must_be_written_by_the_requester(desk: Desk, world: World) -> None:
    cid, order_id, postcode = _customer_with_order(world)
    identity = desk.authority.open_case("CASE-AGENT", "new.address@courriel.example")
    with pytest.raises(ScopeRefused, match="actually wrote"):
        desk.authority.grant_subject(identity, cid, "Please send my data.", order_id, postcode)
    # The same values in a later reply from the requester are fine.
    desk.notifier.receive(
        "CASE-AGENT", "new.address@courriel.example", f"{order_id}, {postcode}", desk.clock()
    )
    assert desk.authority.grant_subject(identity, cid, "Please send my data.", order_id, postcode)


def test_evidence_must_match_the_customer(desk: Desk, world: World) -> None:
    cid, order_id, postcode = _customer_with_order(world)
    other = next(c for c in world.customers if c.customer_id != cid)
    identity = desk.authority.open_case("CASE-WRONG", "someone@courriel.example")
    text = f"order {order_id}, postcode {postcode}, wrong postcode 99999"
    with pytest.raises(ScopeRefused, match="do not match"):
        desk.authority.grant_subject(identity, other.customer_id, text, order_id, postcode)
    with pytest.raises(ScopeRefused, match="do not match"):
        desk.authority.grant_subject(identity, cid, text, order_id, "99999")


def test_generated_impostors_cannot_get_a_grant(desk: Desk, world: World) -> None:
    cases = {c.case_id: c for c in world.cases}
    impostors = [t for t in world.truths if "impostor" in t.difficulties]
    assert impostors
    for t in impostors:
        case = cases[t.case_id]
        assert t.subject_id
        identity = desk.authority.open_case(t.case_id, case.sender)
        with pytest.raises(ScopeRefused):
            desk.authority.grant_subject(identity, t.subject_id, case.text, None, t.requester.knows_postcode)


# --- the scope test --------------------------------------------------------------------------------------


def test_scoped_discovery_returns_exactly_the_subjects_records(desk: Desk, world: World) -> None:
    """For every case with a pack: everything reachable is the subject's, and nothing of theirs is missed."""
    truths = {t.case_id: t for t in world.truths}
    for case_id, subject in released_cases(world):  # distinct case IDs keep the cases' links apart
        truth = truths[case_id]
        token = subject_token(desk, world, subject, case_id)
        before = discover(desk, token)
        assert not foreign(before, world, subject), case_id
        prove_links(desk, token, truth)
        after = discover(desk, token)
        assert not foreign(after, world, subject), case_id
        assert after == set(truth.records), case_id
        if truth.links_needing_proof:
            assert before < after, f"{case_id}: records under the second address should need proof"


def test_token_of_a_cannot_read_records_of_b(desk: Desk, world: World) -> None:
    who = owners(world)
    subjects = [cid for _, cid in released_cases(world)][:25]
    readers: dict[str, Callable[[str, RecordKey], object]] = {
        "crm": lambda tok, k: desk.crm.read_record(tok, k.table, k.record_id),
        "orders": lambda tok, k: desk.orders.read_record(tok, k.table, k.record_id),
        "helpdesk": lambda tok, k: desk.helpdesk.read_ticket(tok, k.record_id),
        "weblogs": lambda tok, k: desk.weblogs.read_record(tok, k.record_id),
    }
    for a, b in pairwise(subjects):
        token_a = subject_token(desk, world, a, f"CASE-{a}")
        records_b = [
            RecordKey.model_validate({"system": s, "table": t, "record_id": r})
            for (s, t, r), owner in who.items()
            if owner == b and t != "messages"
        ]
        assert records_b
        for key in records_b:
            with pytest.raises(ScopeRefused):
                readers[key.system.value](token_a, key)
        with pytest.raises(ScopeRefused):
            desk.helpdesk.search_tickets(token_a, customer_email(world, b))


def test_lookalikes_are_never_reachable(desk: Desk, world: World) -> None:
    checked = 0
    for t in world.truths:
        if not (t.records and t.subject_id and t.lookalikes):
            continue
        token = subject_token(desk, world, t.subject_id, f"LOOK-{t.case_id}")
        assert not discover(desk, token) & set(t.lookalikes), t.case_id
        checked += 1
    assert checked >= 20


# --- proof of control (mcp.subject_scope, R-NO-FOREIGN-DATA) --------------------------------------------


def _case_with(world: World, predicate: Callable[[str], bool], kind: CaseKind = CaseKind.STANDARD) -> str:
    for t in world.truths:
        releasable = t.kind is kind and t.expected_resolution is Resolution.RELEASE_PACK
        if releasable and t.subject_id and predicate(t.subject_id):
            return t.subject_id
    raise AssertionError("no such case in the generated world")


def _ticket_mentioning(desk: Desk, token: str, address: str) -> str:
    for r in desk.helpdesk.search_tickets(token, None).records:
        messages = desk.helpdesk.read_ticket(token, r.record_id).messages if r.table == "tickets" else []
        if any(address in m.body.lower() for m in messages):
            return r.record_id
    raise AssertionError(f"{address} not mentioned")


def _partner_emails(world: World) -> dict[str, str]:
    """subject -> email of a relative named in their ticket (someone who is not a customer)."""
    customers = {c.email for c in world.customers}
    out: dict[str, str] = {}
    for t in world.truths:
        for s in t.redactions:
            if "@" in s.text and s.text not in customers and t.subject_id:
                out.setdefault(t.subject_id, s.text)
    return out


def test_link_needs_evidence_in_the_subjects_own_messages(desk: Desk, world: World) -> None:
    second = {t.subject_id: t.links_needing_proof[0] for t in world.truths if t.links_needing_proof}
    subject = next(iter(second))
    assert subject
    token = subject_token(desk, world, subject)
    other_ticket = next(t.ticket_id for t in world.tickets if t.customer_id not in (None, subject))
    with pytest.raises(ScopeRefused, match="not part of this case's scope"):
        desk.authority.start_link(token, second[subject], other_ticket)
    mention = _ticket_mentioning(desk, token, second[subject])
    with pytest.raises(ScopeRefused, match="does not appear"):
        desk.authority.start_link(token, "nobody.here@courriel.example", mention)
    assert not desk.notifier.mailbox("nobody.here@courriel.example")


def test_a_relatives_address_cannot_be_linked_without_their_mailbox(desk: Desk, world: World) -> None:
    partners = _partner_emails(world)
    subject = _case_with(world, lambda s: s in partners)
    address = partners[subject]
    token = subject_token(desk, world, subject)
    desk.authority.start_link(token, address, _ticket_mentioning(desk, token, address))
    # The code went to the relative's mailbox; the requester can only guess.
    real = latest_code(desk, address)
    for k in range(1, 4):
        with pytest.raises(ScopeRefused, match="wrong code"):
            desk.authority.complete_link(token, address, f"{(int(real) + k) % 10**6:06d}")
    with pytest.raises(ScopeRefused, match="no confirmation code is pending"):
        desk.authority.complete_link(token, address, real)  # void after three wrong tries, even if right now
    assert address not in desk.ctx.resolver.resolve(token).emails


def test_an_address_of_another_customer_is_never_linked(desk: Desk, world: World) -> None:
    truth = next(t for t in world.truths if t.kind is CaseKind.ADV_INJECTION_RECORD)
    assert truth.subject_id
    customers = {c.email for c in world.customers}
    target = next(s.text for s in truth.redactions if s.text in customers)  # planted by the injection
    token = subject_token(desk, world, truth.subject_id)
    with pytest.raises(ScopeRefused, match="another customer"):
        desk.authority.start_link(token, target, _ticket_mentioning(desk, token, target))
    assert not desk.notifier.mailbox(target)


def test_codes_expire_after_seven_days(desk: Desk, world: World) -> None:
    truth = next(t for t in world.truths if t.links_needing_proof and t.kind is CaseKind.STANDARD)
    assert truth.subject_id
    address = truth.links_needing_proof[0]
    token = subject_token(desk, world, truth.subject_id)
    desk.authority.start_link(token, address, _ticket_mentioning(desk, token, address))
    code = latest_code(desk, address)
    desk.clock.advance(timedelta(days=7))
    token = subject_token(desk, world, truth.subject_id)  # the old grant expired too; the case resumes
    with pytest.raises(ScopeRefused, match="expired"):
        desk.authority.complete_link(token, address, code)


def test_a_new_code_replaces_the_old_one(desk: Desk, world: World) -> None:
    truth = next(t for t in world.truths if t.links_needing_proof and t.kind is CaseKind.STANDARD)
    assert truth.subject_id
    address = truth.links_needing_proof[0]
    token = subject_token(desk, world, truth.subject_id)
    ticket = _ticket_mentioning(desk, token, address)
    desk.authority.start_link(token, address, ticket)
    first = latest_code(desk, address)
    desk.authority.start_link(token, address, ticket)
    second = latest_code(desk, address)
    if first != second:
        with pytest.raises(ScopeRefused, match="wrong code"):
            desk.authority.complete_link(token, address, first)
    desk.authority.complete_link(token, address, second)
    assert address in desk.ctx.resolver.resolve(token).emails


def test_at_most_n_links_per_case(desk: Desk, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    partners = _partner_emails(world)
    seconds = {t.subject_id: t.links_needing_proof[0] for t in world.truths if t.links_needing_proof}
    subject = next((s for s in seconds if s in partners), None)
    if subject is None:
        pytest.skip("no subject with both a second address and a relative's address in this seed")
    monkeypatch.setattr(authority_module, "MAX_LINKS_PER_CASE", 1)
    token = subject_token(desk, world, subject)
    address = seconds[subject]
    desk.authority.start_link(token, address, _ticket_mentioning(desk, token, address))
    desk.authority.complete_link(token, address, latest_code(desk, address))
    with pytest.raises(ScopeRefused, match="at most 1"):
        desk.authority.start_link(
            token, partners[subject], _ticket_mentioning(desk, token, partners[subject])
        )


def test_a_shared_browser_is_not_linked(data: DataPaths, world: World, tmp_path: Path) -> None:
    """A cookie that two accounts logged in with links neither person's anonymous page views."""
    copy = DataPaths(tmp_path / "data")
    shutil.copytree(data.root, copy.root)
    con = sqlite3.connect(copy.db(System.WEBLOGS))
    a, cookie = con.execute(
        "SELECT account_id, cookie_id FROM events WHERE account_id IS NOT NULL ORDER BY event_id LIMIT 1"
    ).fetchone()
    other = next(c.customer_id for c in world.customers if c.customer_id != a)
    anonymous = {
        r[0]
        for r in con.execute(
            "SELECT event_id FROM events WHERE cookie_id = ? AND account_id IS NULL", (cookie,)
        )
    }
    con.execute(
        "INSERT INTO events VALUES ('E9999999', '2025-12-30T10:00:00', ?, ?, '192.0.2.1', '/compte', 'test')",
        (cookie, other),
    )
    con.commit()
    con.close()
    desk = make_desk(copy, tmp_path / "runtime")
    token = subject_token(desk, world, a)
    found = {k.record_id for k in discover(desk, token) if k.table == "events"}
    assert anonymous and not anonymous & found
    assert cookie not in desk.ctx.resolver.resolve(token).cookies


# --- the checks above can fail ----------------------------------------------------------------------------


class LeakyHelpdesk(HelpdeskService):
    """A deliberately broken server that ignores the scope (broken version for 'testing the tests')."""

    def search_tickets(self, token: str, email: str | None):  # type: ignore[no-untyped-def]
        from article15.company.store import read_only
        from article15.mcp_servers.common import RecordSummary, SearchResult
        from article15.schemas import System

        self.ctx.resolver.resolve(token).require_subject()
        with read_only(self.ctx.paths.db(System.HELPDESK)) as con:
            rows = con.execute("SELECT ticket_id FROM tickets").fetchall()
        return SearchResult(
            records=[
                RecordSummary(system=System.HELPDESK, table="tickets", record_id=r[0], summary="")
                for r in rows
            ],
            total=len(rows),
        )


def test_the_foreign_record_check_catches_a_leaky_server(desk: Desk, world: World) -> None:
    _, subject = released_cases(world)[0]
    desk.helpdesk = LeakyHelpdesk(desk.ctx)
    token = subject_token(desk, world, subject)
    assert foreign(discover(desk, token), world, subject)


def test_the_exact_match_check_catches_a_skipped_system(desk: Desk, world: World) -> None:
    truth = next(
        t
        for t in world.truths
        if t.subject_id and not t.links_needing_proof and any(k.system is System.WEBLOGS for k in t.records)
    )
    assert truth.subject_id
    token = subject_token(desk, world, truth.subject_id)
    assert discover(desk, token) == set(truth.records)
    without_weblogs = {k for k in discover(desk, token) if k.system is not System.WEBLOGS}
    assert without_weblogs != set(truth.records)


def test_link_codes_replay_under_a_fixed_key_and_differ_under_another() -> None:
    # Recorded runs need the same code every run; the web app keys codes per run, so no visitor computes one.
    fixed = keyed_link_code(b"a15-recorded-runs")
    code = fixed("A15-011", "someone@webpost.example")
    assert code == fixed("A15-011", "someone@webpost.example") and len(code) == 6 and code.isdigit()
    assert fixed("A15-011", "other@webpost.example") != code
    assert keyed_link_code(b"another key")("A15-011", "someone@webpost.example") != code
    assert len(random_link_code("A15-011", "someone@webpost.example")) == 6
