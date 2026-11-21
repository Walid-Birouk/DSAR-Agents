"""Generated contact data must come from ranges reserved for examples and fiction."""

import pytest

from article15.company import fiction
from article15.company.models import World


def test_arcep_fiction_roots_match_the_numbering_plan() -> None:
    # ARCEP, Plan national de numérotation, version of 1 January 2026, p. 55/67.
    assert fiction.PHONE_ROOTS == ("019900", "026191", "035301", "046571", "053649", "063998")


FICTIONAL_PHONES = ("06 39 98 12 34", "+33 6 39 98 12 34", "01.99.00.00.01")
REAL_LOOKING_PHONES = ("06 12 34 56 78", "03 53 20 12 34", "06 39 98 12")
FICTIONAL_EMAILS = ("lea.martin@courriel.example", "x@ferlane.example")
OTHER_EMAILS = ("lea.martin@gmail.com", "lea@example.com", "not an email")
FICTIONAL_IPS = ("192.0.2.7", "198.51.100.254", "203.0.113.1")
OTHER_IPS = ("8.8.8.8", "10.0.0.1", "nope")


@pytest.mark.parametrize(
    ("value", "expected"),
    [*((v, True) for v in FICTIONAL_PHONES), *((v, False) for v in REAL_LOOKING_PHONES)],
)
def test_phone_validator(value: str, expected: bool) -> None:
    assert fiction.is_fictional_phone(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [*((v, True) for v in FICTIONAL_EMAILS), *((v, False) for v in OTHER_EMAILS)],
)
def test_email_validator(value: str, expected: bool) -> None:
    assert fiction.is_fictional_email(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [*((v, True) for v in FICTIONAL_IPS), *((v, False) for v in OTHER_IPS)],
)
def test_ip_validator(value: str, expected: bool) -> None:
    assert fiction.is_fictional_ip(value) is expected


def test_format_phone_refuses_numbers_outside_the_fiction_blocks() -> None:
    assert fiction.format_phone("063998", 1234) == "06 39 98 12 34"
    with pytest.raises(ValueError):
        fiction.format_phone("061234", 1)
    with pytest.raises(ValueError):
        fiction.format_phone("063998", 10000)


def test_every_generated_contact_detail_is_fictional(world: World) -> None:
    emails = [c.email for c in world.customers]
    emails += [m for p in world.people for m in p.mailboxes]
    emails += [o.guest_email for o in world.orders if o.guest_email]
    emails += [t.requester_email for t in world.tickets]
    emails += [c.sender for c in world.cases]
    assert all(fiction.is_fictional_email(e) for e in emails), [
        e for e in emails if not fiction.is_fictional_email(e)
    ]
    assert all(fiction.is_fictional_phone(c.phone) for c in world.customers)
    assert all(fiction.is_fictional_ip(e.ip) for e in world.events)
