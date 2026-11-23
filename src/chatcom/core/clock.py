"""Local wall-clock times to aware datetimes, shared by every adapter.

`localize` places a naive time in the show's zone and settles the repeated hour
of a DST fall-back from the time before it. `Synthetic` gives `day` and `order`
rows a time that is strictly increasing in chat order, to the millisecond,
anchored at 12:00 on the date the source shows (schemas/message.schema.json)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

NOON = time(12, 0)
STEP = timedelta(milliseconds=1)


def localize(naive: datetime, tz: tzinfo, prev_utc: datetime | None) -> datetime:
    a0 = naive.replace(tzinfo=tz, fold=0)
    a1 = naive.replace(tzinfo=tz, fold=1)
    if a0.utcoffset() == a1.utcoffset() or prev_utc is None:
        return a0
    # The repeated hour of a DST fall-back: once time has passed the first
    # pass through it, the export is in the second one.
    if a0.astimezone(timezone.utc) < prev_utc:
        return a1
    return a0


class Synthetic:
    """Times for rows the source shows without a clock time."""

    def __init__(self, tz: tzinfo):
        self.tz = tz
        self.prev: datetime | None = None

    def seen(self, aware: datetime) -> None:
        """Record a real (timed) row, so a later synthetic row comes after it."""
        self.prev = aware

    def next(self, day: date) -> datetime:
        base = datetime.combine(day, NOON).replace(tzinfo=self.tz)
        t = base if self.prev is None or self.prev + STEP <= base else self.prev + STEP
        self.prev = t
        return t
