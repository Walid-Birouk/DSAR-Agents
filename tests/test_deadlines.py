from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from article15.case.deadlines import add_months, deadlines_for, due_date, is_working_day


def test_edpb_example_month_end() -> None:
    # EDPB Guidelines 01/2022 para. 160: received 31 August -> 30 September (if a working day).
    assert add_months(date(2026, 8, 31), 1) == date(2026, 9, 30)
    assert due_date(date(2026, 8, 31)) == date(2026, 9, 30)  # Wednesday


def test_weekend_moves_to_next_working_day() -> None:
    # Received Monday 10 Aug 2026 -> 10 Sep 2026 is a Thursday: unchanged.
    assert due_date(date(2026, 8, 10)) == date(2026, 9, 10)
    # Received 3 Oct 2026 -> 3 Nov 2026 is a Tuesday; received 7 Oct -> 7 Nov is a Saturday -> Monday 9 Nov.
    assert due_date(date(2026, 10, 7)) == date(2026, 11, 9)


def test_public_holiday_moves_to_next_working_day() -> None:
    # Received 11 Oct 2026 -> 11 Nov 2026 is Armistice Day (French public holiday) -> Thursday 12 Nov.
    assert due_date(date(2026, 10, 11)) == date(2026, 11, 12)
    # Received 25 Nov 2026 -> 25 Dec 2026 (Christmas, Friday) -> Monday 28 Dec.
    assert due_date(date(2026, 11, 25)) == date(2026, 12, 28)


def test_january_31_to_february() -> None:
    assert add_months(date(2027, 1, 31), 1) == date(2027, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)  # leap year


def test_extension_is_three_months_from_receipt() -> None:
    d = deadlines_for(date(2026, 8, 31))
    assert d.extended_due == date(2026, 11, 30)  # Monday
    assert d.extension_notice_by == d.due


@given(st.dates(min_value=date(2020, 1, 1), max_value=date(2035, 12, 31)))
def test_due_date_properties(received: date) -> None:
    due = due_date(received)
    nominal = add_months(received, 1)
    assert is_working_day(due)
    assert due >= nominal
    # never more than a long weekend plus holidays past the nominal date
    assert due - nominal <= timedelta(days=5)
    # and every day between nominal and due is a non-working day
    day = nominal
    while day < due:
        assert not is_working_day(day)
        day += timedelta(days=1)
    assert deadlines_for(received).extended_due > due
