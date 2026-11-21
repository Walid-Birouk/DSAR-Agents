"""The simulated case clock.

A run happens "on" the day the request was received, so deadlines, grant expiry and message times are the same
on every run of the same case, live or replayed. The clock moves only when the simulation says so (for
example, the requester answers a day later).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

CASE_START_UTC = time(8, 0)
REQUESTER_REPLY_DELAY = timedelta(days=1)


class SimClock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta

    @classmethod
    def for_case(cls, received_on: date) -> SimClock:
        return cls(datetime.combine(received_on, CASE_START_UTC, tzinfo=UTC))
