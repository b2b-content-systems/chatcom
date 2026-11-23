"""Local wall-clock times to aware datetimes, shared by every adapter.

`localize` places a naive time in the show's zone and settles the repeated hour
of a DST fall-back from the time before it. `fill` gives `day` and `order` rows
a time that keeps chat order, to the millisecond, anchored at 12:00 on the date
the source shows (schemas/message.schema.json)."""
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


def fill(times: list[datetime | None], days: list[date], tz: tzinfo) -> list[datetime]:
    """Times for the rows with none (None in `times`), each on its row's day.

    A run of such rows on one day goes just after the row before it when that
    row is on the same day, else just before the next timed row on that day,
    else from 12:00. So a row with no time between two timed rows stays between
    them, and every row is after the one before it unless the source itself
    goes back in time."""
    out: list[datetime | None] = list(times)
    n, i = len(times), 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        day = days[i]
        j = i
        while j < n and times[j] is None and days[j] == day:
            j += 1
        prev = out[i - 1] if i else None
        nxt = times[j] if j < n else None
        if prev is not None and prev.astimezone(tz).date() == day:
            start = prev + STEP
        elif nxt is not None and nxt.astimezone(tz).date() == day:
            start = nxt - STEP * (j - i)
        else:
            start = datetime.combine(day, NOON).replace(tzinfo=tz)
            if prev is not None and prev + STEP > start:
                start = prev + STEP
        for k in range(i, j):
            out[k] = start + STEP * (k - i)
        i = j
    return out  # type: ignore[return-value]
