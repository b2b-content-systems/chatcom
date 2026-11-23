"""Which messages an episode covers, and where the next one starts.

Episode 1 covers the last 7 days of the export (counted back from its last
message), or --since/--until. Every later episode starts right after the
cursor (the exact last message used last time) and is capped at 14 days;
older unread messages are skipped and the gap is logged. Messages are
de-duplicated by id, so overlapping exports are safe."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from chatcom.core.errors import ChatcomError
from chatcom.core.messages import utc_minute

FIRST_EPISODE_DAYS = 7
MAX_DAYS = 14


@dataclass
class Window:
    messages: list[dict]
    start: datetime
    end: datetime
    cursor: dict
    gap: dict | None
    excluded: int

    def log_entry(self) -> dict:
        return {"from": self.start.isoformat(timespec="seconds"), "to": self.end.isoformat(timespec="seconds"),
                "cursor": self.cursor, "message_count": len(self.messages), "gap": self.gap}


def _t(m: dict) -> datetime:
    return datetime.fromisoformat(m["time"])


def dedupe(messages: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for m in sorted(messages, key=_t):
        if m["id"] in seen:
            continue
        seen.add(m["id"])
        out.append(m)
    return out


def apply_exclusions(messages: list[dict], exclude: list[str],
                     aliases: dict[str, list[str]] | None = None) -> tuple[list[dict], int]:
    """Drop excluded senders and blank @mentions of them (by name or any
    alias), before any model call."""
    if not exclude:
        return messages, 0
    names = {e.casefold() for e in exclude}
    forms = set(exclude)
    for canon, others in (aliases or {}).items():
        if canon.casefold() in names:
            forms.update(others)
    # (?!\w), not \b: a name ending in an emoji or "." has no word boundary.
    pats = [re.compile(r"@" + re.escape(f) + r"(?!\w)", re.I) for f in sorted(forms, key=len, reverse=True)]
    out, dropped = [], 0
    for m in messages:
        if m["sender"].casefold() in names:
            dropped += 1
            continue
        text = m["text"]
        for p in pats:
            text = p.sub("@[someone]", text)
        out.append(m if text == m["text"] else {**m, "text": text})
    return out, dropped


def select(messages: list[dict], previous_cursor: dict | None, *, since: datetime | None = None,
           until: datetime | None = None, max_days: int = MAX_DAYS, bottle: bool = False,
           exclude: list[str] | None = None, aliases: dict[str, list[str]] | None = None) -> Window:
    msgs = dedupe(messages)
    if not msgs:
        raise ChatcomError("CHATCOM_E_EMPTY_EXPORT", "the export holds no messages",
                           "Export the chat again from WhatsApp (More, Export chat, Without media).")
    if until:
        msgs = [m for m in msgs if _t(m) <= until]
    if previous_cursor is None:
        last = _t(msgs[-1]) if msgs else until
        start = since or (last - timedelta(days=FIRST_EPISODE_DAYS))
        new = [m for m in msgs if _t(m) >= start]
        gap = None
    else:
        new = _after_cursor(msgs, previous_cursor)
        if since:
            new = [m for m in new if _t(m) >= since]
        gap = None
        if new:
            cap_start = _t(new[-1]) - timedelta(days=max_days)
            older = [m for m in new if _t(m) < cap_start]
            if older:
                gap = {"from": _t(older[0]).isoformat(timespec="seconds"),
                       "to": _t(older[-1]).isoformat(timespec="seconds"), "skipped": len(older)}
                new = [m for m in new if _t(m) >= cap_start]
    pre = new
    new, excluded = apply_exclusions(new, exclude or [], aliases)
    if not new:
        if bottle and previous_cursor is not None and msgs:
            # A bottle episode with no new lines keeps the old cursor.
            ref = msgs[-1]
            return Window([], _t(ref), _t(ref), dict(previous_cursor), None, excluded)
        last = msgs[-1]["time"] if msgs else "none in the chosen range"
        if previous_cursor is None or not msgs:
            raise ChatcomError("CHATCOM_E_STALE_EXPORT",
                               f"no messages in the chosen window (export ends {last})",
                               "Widen --since/--until, or export the chat again.",
                               context={"export_last": last})
        raise ChatcomError("CHATCOM_E_STALE_EXPORT",
                           f"no new messages after the last episode (export ends {last})",
                           "Export the chat again so it includes this week, or pass --bottle for a callback episode.",
                           context={"cursor": previous_cursor, "export_last": last})
    # The cursor is the last message read, excluded or not, so an excluded
    # person's lines are never re-read next week.
    end_msg = pre[-1]
    cursor = {"minute": utc_minute(_t(end_msg)), "hash": end_msg["id"]}
    return Window(new, _t(new[0]), _t(end_msg), cursor, gap, excluded)


def _after_cursor(msgs: list[dict], cursor: dict) -> list[dict]:
    for i, m in enumerate(msgs):
        if m["id"] == cursor["hash"]:
            return msgs[i + 1:]
    # The exact message is gone (a different export range): resume after its minute.
    return [m for m in msgs if utc_minute(_t(m)) > cursor["minute"]]
