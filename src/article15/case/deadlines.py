"""Response deadlines for access requests.

GDPR Art. 12(3): answer within one month of receipt; extendable by two further months.
Counting follows the EDPB Guidelines 01/2022 (v2.1), paras. 160-161:
  * the deadline is the same date in the following month;
  * if that date does not exist (e.g. 31 August -> no 31 September), it is the last day of that month;
  * if the last day falls on a weekend or public holiday, the deadline is the next working day.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from functools import cache

import holidays

BASE_MONTHS = 1
EXTENSION_MONTHS = 2


@cache
def _public_holidays(country: str) -> holidays.HolidayBase:
    return holidays.country_holidays(country)


def add_months(day: date, months: int) -> date:
    """Same date `months` later; clamps to the last day of the target month."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def is_working_day(day: date, country: str = "FR") -> bool:
    return day.weekday() < 5 and day not in _public_holidays(country)


def next_working_day(day: date, country: str = "FR") -> date:
    while not is_working_day(day, country):
        day += timedelta(days=1)
    return day


def due_date(received: date, months: int = BASE_MONTHS, country: str = "FR") -> date:
    """Last day to answer a request received on `received`."""
    if months < 1:
        raise ValueError("months must be >= 1")
    return next_working_day(add_months(received, months), country)


@dataclass(frozen=True)
class Deadlines:
    received: date
    due: date
    extended_due: date
    extension_notice_by: date
    """An extension must be announced to the requester within the first month (Art. 12(3))."""


def deadlines_for(received: date, country: str = "FR") -> Deadlines:
    due = due_date(received, BASE_MONTHS, country)
    return Deadlines(
        received=received,
        due=due,
        extended_due=due_date(received, BASE_MONTHS + EXTENSION_MONTHS, country),
        extension_notice_by=due,
    )
