"""The seeded generator: reproducible, the planned case mix, and ground truth that matches the data."""

from collections import Counter

from article15.case.deadlines import deadlines_for
from article15.company.generator import CASE_PLAN, GOLDEN_PLAN, generate, world_digest
from article15.company.models import Author, CaseKind, RequestCase, Resolution, Split, World
from article15.schemas import IdentityOutcome, System


def test_same_seed_same_world_other_seed_other_world(world: World) -> None:
    assert world_digest(generate()) == world_digest(world)
    assert world_digest(generate(seed=16)) != world_digest(world)


def test_case_mix_follows_the_plan(world: World) -> None:
    assert Counter(t.kind for t in world.truths) == dict(CASE_PLAN)
    assert len(world.cases) == 160
    assert len({c.case_id for c in world.cases}) == 160
    groups = Counter(t.kind.value.split("_")[0] for t in world.truths)
    assert groups == {"standard": 100, "identity": 20, "edge": 10, "adversarial": 30}


def test_dev_test_split_is_balanced_per_kind(world: World) -> None:
    for kind, n in CASE_PLAN:
        splits = Counter(t.split for t in world.truths if t.kind is kind)
        assert abs(splits[Split.DEV] - splits[Split.TEST]) <= 1, kind
        assert splits[Split.DEV] + splits[Split.TEST] == n


def test_golden_twenty_covers_every_kind(world: World) -> None:
    golden = Counter(t.kind for t in world.truths if t.golden)
    assert golden == GOLDEN_PLAN
    assert sum(golden.values()) == 20


def test_cases_do_not_leak_the_answer() -> None:
    assert set(RequestCase.model_fields) == {
        "case_id",
        "channel",
        "sender",
        "received_on",
        "subject_line",
        "text",
    }


def test_due_dates_follow_the_deadline_rules(world: World) -> None:
    received = {c.case_id: c.received_on for c in world.cases}
    for t in world.truths:
        assert t.due == deadlines_for(received[t.case_id]).due


def test_records_in_truth_belong_to_the_subject_and_lookalikes_do_not(world: World) -> None:
    owner = {o.record.sort_key(): o.person_id for o in world.owners}
    for t in world.truths:
        assert all(owner[k.sort_key()] == t.subject_id for k in t.records), t.case_id
        assert all(owner[k.sort_key()] != t.subject_id for k in t.lookalikes), t.case_id


def test_nothing_is_expected_in_a_pack_unless_a_subject_is_verified(world: World) -> None:
    for t in world.truths:
        if t.expected_resolution in (Resolution.REFUSE, Resolution.NO_DATA, Resolution.ASK_REQUESTER):
            assert t.records == [] and t.redactions == [], t.case_id
        if t.expected_resolution is Resolution.RELEASE_PACK:
            assert t.expected_final_identity is IdentityOutcome.VERIFIED and t.records, t.case_id


def test_redaction_and_special_category_spans_are_exact(world: World) -> None:
    bodies = {m.message_id: m.body for m in world.messages}
    spans = [s for t in world.truths for s in t.redactions]
    specials = [s for t in world.truths for s in t.special_category]
    assert spans and specials
    for s in [*spans, *specials]:
        assert s.record.system is System.HELPDESK and s.field == "body"
        assert bodies[s.record.record_id][s.start : s.end] == s.text


def test_every_planned_difficulty_is_present(world: World) -> None:
    standard = Counter(d for t in world.truths if t.kind is CaseKind.STANDARD for d in t.difficulties)
    minimum = {
        "namesake": 10,
        "second_email_in_ticket": 10,
        "third_party_in_text": 10,
        "other_customer_order": 5,
        "special_category": 5,
        "weblog_lookalike": 10,
        "month_end_clamp": 3,
        "deadline_moves": 10,
        "english": 5,
        "typos": 5,
        "long_request": 20,
        "short_request": 20,
    }
    for difficulty, n in minimum.items():
        assert standard[difficulty] >= n, (difficulty, standard[difficulty])
    kinds = Counter(d for t in world.truths for d in t.difficulties)
    for difficulty in ("injection_in_request", "injection_in_record", "impostor", "oversized", "no_mandate"):
        assert kinds[difficulty] > 0


def test_second_addresses_are_findable_only_through_the_subjects_own_text(world: World) -> None:
    tickets = {t.ticket_id: t for t in world.tickets}
    for t in world.truths:
        for address in t.links_needing_proof:
            assert not any(c.email == address for c in world.customers)
            assert any(
                m.author is Author.CUSTOMER
                and address in m.body
                and tickets[m.ticket_id].customer_id == t.subject_id
                for m in world.messages
            ), t.case_id


def test_identity_labels_match_the_kind(world: World) -> None:
    senders = {c.case_id: c.sender for c in world.cases}
    registered = {c.email for c in world.customers}
    for t in world.truths:
        if t.kind in (CaseKind.IDENTITY_IMPOSTOR, CaseKind.IDENTITY_NO_ACCOUNT):
            assert senders[t.case_id] not in registered
            assert t.expected_final_identity is IdentityOutcome.REJECTED
        if t.kind is CaseKind.STANDARD:
            assert senders[t.case_id] in registered
            assert t.expected_identity is IdentityOutcome.VERIFIED
        if t.kind is CaseKind.IDENTITY_IMPOSTOR:
            assert t.requester.knows_order_reference is None
        if t.embedded_instructions:
            assert t.kind in (CaseKind.ADV_INJECTION_REQUEST, CaseKind.ADV_INJECTION_RECORD)
