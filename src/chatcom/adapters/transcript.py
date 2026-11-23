"""The transcript adapter: the one plain-text chat format a person can read,
type, edit or paste (docs/transcript-format.md), and its writer.

    # source: paste
    # speakers: Mike, Dave
    --- 2026-01-10 ---
    > Dave: who's driving
    [2026-01-10 18:02] Mike: not me <image> (reactions: 😂 Dave, 👍 2)
      a continuation line, indented two spaces
    Dave: a line with no time takes the date above it

`write()` then `parse()` gives back the same messages; the writer escapes any
line that would read as structure (tests/test_transcript.py checks this over a
hostile corpus). A file is read as a transcript only when its first line is a
`# source:` or `# speakers:` header, or the operator passes --source transcript."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters.source import ParseResult
from chatcom.core.clock import fill, localize
from chatcom.core.errors import ChatcomError
from chatcom.core.masking import alias_map, canonical, is_phone, mask_numbers, phone_label
from chatcom.core.messages import (TIMED, body_token, context_hash, message_hash, normalize_text,
                                   utc_minute)

MAX_BYTES = 64 * 1024 * 1024
MAX_NAME = 40
KINDS = ("image", "video", "audio", "sticker", "gif", "document", "contact", "location", "poll", "other")
URL_SCHEMES = {"http", "https", "ftp", "mailto", "tel", "sms", "file", "www", "whatsapp", "tg", "signal"}

_HEADER = re.compile(r"^#\s*([A-Za-z][\w-]*)\s*:\s*(.*)$")
_ORIGIN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_DATE_LINE = re.compile(r"^---\s*(\d{4})-(\d{2})-(\d{2})\s*---$")
_STAMP = re.compile(r"^\[(?:(\d{4})-(\d{2})-(\d{2})\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s+(.*)$", re.S)
_PLACEHOLDER = re.compile(r"^<(" + "|".join(KINDS) + r"|deleted)>(?: (.*))?$", re.S)
_REACTIONS = re.compile(r" ?\(reactions: ((?:[^()\\\n]|\\.)*)\)$")
_COUNTED = re.compile(r"^(.*) ×(\d{1,6})$")
MAX_COUNT_DIGITS = 6  # a longer number is a name (and masked), never a count
_STRUCTURE = ("\\", "#", ">", "-", "[", "<")
NO_REACTIONS = "-"


def _invalid(line: int, what: str) -> ChatcomError:
    return ChatcomError("CHATCOM_E_TRANSCRIPT_INVALID", f"transcript line {line}: {what}",
                        f"Fix line {line} (the format is in docs/transcript-format.md), then run the command again.",
                        context={"line": line})


def valid_name(name: str, strict: bool = False) -> bool:
    """A speaker name: what can stand before `: ` and read back the same.
    `strict` also refuses web address schemes ("https"), for guessing names
    when there is no `# speakers:` line; a listed speaker may be "Signal"."""
    return (0 < len(name) <= MAX_NAME and name == normalize_text(name) and "\n" not in name
            and ": " not in name and not name.endswith(":") and not name.startswith(_STRUCTURE)
            and not name.isdigit() and not (strict and name.casefold() in URL_SCHEMES))


def _split_name(s: str, strict: bool = False) -> tuple[str, str] | None:
    """(name, text) when s starts with `Name: `, or is `Name:` alone."""
    i = s.find(": ")
    if i < 0:
        return (s[:-1], "") if s.endswith(":") and valid_name(s[:-1], strict) else None
    name, text = s[:i], s[i + 2:]
    return (name, text) if valid_name(name, strict) else None


# ---- name lists (speakers header, reactions) --------------------------------

_ITEM_SPECIAL = ("\\", ",", "(", ")", "×")


def _esc_item(s: str) -> str:
    """A name in a list: `\\`, `,`, `(`, `)` and `×` escaped, and an all-digit
    name escaped so it cannot read as a count."""
    out = "".join("\\" + ch if ch in _ITEM_SPECIAL else ch for ch in s)
    return "\\" + out if s.isdigit() else out


def _unesc(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            out.append(raw[i + 1])
            i += 2
        else:
            out.append(raw[i])
            i += 1
    return "".join(out)


def _split_raw(s: str) -> list[str]:
    """Split on commas that are not escaped; escapes are kept."""
    out, cur, i = [], [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if s[i] == ",":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    out.append("".join(cur).strip())
    return out


def _split_items(s: str) -> list[str]:
    return [_unesc(x) for x in _split_raw(s)]


def _parse_reactions(body: str) -> list[dict] | None:
    """Items read on the raw text, before escapes are removed, so an escaped
    name can never read as a count."""
    out = []
    for item in _split_raw(body):
        emoji, _, rest = item.partition(" ")
        if not emoji:
            return None
        emoji = _unesc(emoji)
        if not rest:
            out.append({"emoji": emoji, "sender": None, "count": 1})
        elif rest.isdigit() and len(rest) <= MAX_COUNT_DIGITS and int(rest) > 0:
            out.append({"emoji": emoji, "sender": None, "count": int(rest)})
        elif (m := _COUNTED.match(rest)) and int(m[2]) > 0:
            out.append({"emoji": emoji, "sender": _unesc(m[1]), "count": int(m[2])})
        else:
            out.append({"emoji": emoji, "sender": _unesc(rest), "count": 1})
    return out


def _write_reactions(reactions: list[dict]) -> str:
    items = []
    for r in reactions:
        if r.get("sender") is None:
            items.append(f"{_esc_item(r['emoji'])} {r['count']}")
        elif r["count"] == 1:
            items.append(f"{_esc_item(r['emoji'])} {_esc_item(r['sender'])}")
        else:
            items.append(f"{_esc_item(r['emoji'])} {_esc_item(r['sender'])} ×{r['count']}")
    return f"(reactions: {', '.join(items)})"


# ---- parse ----------------------------------------------------------------------

@dataclass
class _Row:
    line: int
    name: str
    first: str
    day: date | None
    clock: tuple[int, int, int | None] | None
    quote: list[str] | None
    extra: list[str] = field(default_factory=list)


def _date(line: int, y: str, mo: str, d: str) -> date:
    try:
        return date(int(y), int(mo), int(d))
    except ValueError:
        raise _invalid(line, f"{y}-{mo}-{d} is not a date") from None


def _header(lines: list[str]) -> tuple[int, str | None, list[str] | None]:
    origin, speakers, i = None, None, 0
    while i < len(lines):
        s = lines[i].strip()
        if not s:
            i += 1
            continue
        if not s.startswith("#"):
            break
        m = _HEADER.match(s)
        if not m:
            raise _invalid(i + 1, "a header line is `# source: NAME` or `# speakers: A, B`")
        key, value = m[1].lower(), m[2].strip()
        if key == "source":
            if not _ORIGIN.match(value):
                raise _invalid(i + 1, f"`# source:` takes one word such as paste, signal or imessage, not {value!r}")
            origin = value
        elif key == "speakers":
            names = _split_items(value)
            bad = [n for n in names if not valid_name(n)]
            if bad or not names:
                raise _invalid(i + 1, f"not a speaker name: {bad[0]!r}" if bad else "`# speakers:` lists no one")
            if len(set(names)) != len(names):
                raise _invalid(i + 1, "a speaker is listed twice")
            speakers = names
        else:
            raise _invalid(i + 1, f"unknown header `# {m[1]}:` (only `# source:` and `# speakers:`)")
        i += 1
    return i, origin, speakers


def _candidates(lines: list[str]) -> set[str]:
    """Without a speakers header, a name must start 2 or more lines."""
    seen: Counter = Counter()
    for ln in lines:
        if ln.startswith((" ", ">", "\\")):
            continue
        st = _STAMP.match(ln)
        hit = _split_name(st[7] if st else ln, strict=True)
        if hit:
            seen[hit[0]] += 1
    return {n for n, c in seen.items() if c >= 2}


def _scan(lines: list[str], start: int, names: set[str]) -> tuple[list[_Row], list[tuple[int, str]]]:
    rows: list[_Row] = []
    unparsed: list[tuple[int, str]] = []
    day: date | None = None
    quote: list[str] | None = None
    quote_at = 0

    def drop_quote():
        nonlocal quote
        if quote is not None:
            unparsed.append((quote_at, "> " + quote[0]))
            quote = None

    def cont(no: int, text: str, raw: str):
        if rows and quote is None:
            rows[-1].extra.append(text)
        else:
            drop_quote()
            unparsed.append((no, raw))

    for no, ln in enumerate(lines[start:], start=start + 1):
        if ln.startswith("  "):
            body = ln[2:]
            cont(no, body[1:] if body.startswith("\\") else body, ln)
            continue
        if not ln.strip():
            if rows and quote is None:
                rows[-1].extra.append("")
            continue
        if (dm := _DATE_LINE.match(ln.strip())):
            drop_quote()
            day = _date(no, dm[1], dm[2], dm[3])
            continue
        if ln.startswith(">  ") and quote is not None:
            body = ln[3:]
            quote.append(body[1:] if body.startswith("\\") else body)
            continue
        if ln == ">" or ln.startswith("> "):
            drop_quote()
            quote, quote_at = [ln[2:]], no
            continue
        st = _STAMP.match(ln)
        hit = _split_name(st[7] if st else ln)
        if hit and hit[0] in names:
            clock, row_day = None, day
            if st:
                if st[1]:
                    # a full timestamp also dates the undated lines after it
                    row_day = day = _date(no, st[1], st[2], st[3])
                h, mi = int(st[4]), int(st[5])
                s = int(st[6]) if st[6] else None
                if h > 23 or mi > 59 or (s is not None and s > 59):
                    raise _invalid(no, f"{st[4]}:{st[5]} is not a time of day")
                clock = (h, mi, s)
            rows.append(_Row(no, hit[0], hit[1], row_day, clock, quote))
            quote = None
            continue
        cont(no, ln[1:] if ln.startswith("\\") else ln, ln)
    drop_quote()
    unparsed.sort()
    return rows, unparsed


def _body(row: _Row) -> tuple[str, str | None, bool, list[dict] | None]:
    """(text, media, deleted, reactions) from a message's raw lines."""
    lines = [row.first, *row.extra]
    while len(lines) > 1 and not lines[-1].strip():
        lines.pop()
    reactions = None
    m = _REACTIONS.search(lines[-1])
    if m:
        if m[1].strip() == NO_REACTIONS:
            lines[-1] = lines[-1][: m.start()]
        elif (parsed := _parse_reactions(m[1])) is not None:
            reactions = parsed
            lines[-1] = lines[-1][: m.start()]
    first, rest = lines[0], lines[1:]
    if first.startswith("\\"):
        return "\n".join([first[1:], *rest]), None, False, reactions
    pm = _PLACEHOLDER.match(first)
    if pm:
        if pm[1] == "deleted":
            return "", None, True, reactions
        return "\n".join([pm[2] or "", *rest]), pm[1], False, reactions
    return "\n".join(lines), None, False, reactions


def parse(text: str, tz_name: str, *, aliases: dict[str, list[str]] | None = None,
          key: bytes | None = None, anchor: date | None = None) -> ParseResult:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise ChatcomError("CHATCOM_E_TIMEZONE", f"unknown timezone {tz_name!r}",
                           "Pass an IANA zone with --tz, for example --tz America/New_York.") from None
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    start, origin, speakers = _header(lines)
    names = set(speakers) if speakers else _candidates(lines[start:])
    rows, unparsed = _scan(lines, start, names)

    amap = alias_map(aliases or {})
    masked: dict[str, str] = {}
    # Rows before the first date shown came before it: they sit on the day
    # before. A transcript with no date at all uses the anchor (the day the
    # file was saved).
    first_day = next((r.day for r in rows if r.day), None)
    anchor = first_day - timedelta(days=1) if first_day else (anchor or date.today())
    ordinals: Counter = Counter()
    def who(n: str) -> str:
        """A name with any phone number inside it masked (a bare number is
        masked whole by canonical/phone_label)."""
        return n if is_phone(n) else mask_numbers(n, amap, key)

    prev_utc: datetime | None = None
    clocked: list[datetime | None] = []
    for row in rows:
        if row.clock and row.day:
            h, mi, sec = row.clock
            aware = localize(datetime(row.day.year, row.day.month, row.day.day, h, mi, sec or 0), tz, prev_utc)
            prev_utc = aware.astimezone(timezone.utc)
            clocked.append(aware)
        else:
            clocked.append(None)
    times = fill(clocked, [r.day or anchor for r in rows], tz)
    prev_key = ("", "")
    run = 0
    msgs: list[dict] = []
    for row, real, aware in zip(rows, clocked, times):
        raw_text, media, deleted, reactions = _body(row)
        text = mask_numbers(normalize_text(raw_text), amap, key)
        name = who(row.name)
        label = phone_label(row.name, key) if is_phone(row.name) else name
        id_label = "" if label.startswith("Speaker ") and len(label) <= 10 else label
        if real is not None:
            precision = "exact" if row.clock[2] is not None else "minute"
            stamp = aware.isoformat(timespec="seconds")
            minute = utc_minute(aware)
            mid = message_hash(id_label, minute, ordinals[(id_label, minute)], text, media, deleted, key)
            ordinals[(id_label, minute)] += 1
        else:
            precision = "day" if row.day else "order"
            stamp = aware.isoformat(timespec="milliseconds")
            token = body_token(text, media, deleted)
            run = run + 1 if (id_label, token) == prev_key else 0
            mid = context_hash(id_label, row.day.isoformat() if row.day else None, text, media, deleted,
                               prev_key[0], prev_key[1], run, key)
        prev_key = (id_label, body_token(text, media, deleted))
        quote = None
        if row.quote is not None:
            q0 = row.quote[0]
            if q0.startswith("\\"):
                qsender, qfirst = None, q0[1:]
            else:
                hit = _split_name(q0)
                # only a speaker starts a quote; anything else is quoted text
                qsender, qfirst = hit if hit and hit[0] in names else (None, q0)
            quote = {"sender": canonical(who(qsender), amap, masked, key) if qsender else None,
                     "text": mask_numbers(normalize_text("\n".join([qfirst, *row.quote[1:]])), amap, key)}
        if reactions:
            reactions = [{**r, "sender": canonical(who(r["sender"]), amap, masked, key) if r["sender"] else None}
                         for r in reactions]
        msgs.append({
            "id": mid,
            "sender": canonical(name, amap, masked, key),
            "time": stamp,
            "time_precision": precision,
            "text": text,
            "media": media,
            "deleted": deleted,
            "edited": False,
            "reply_to": None,
            "reactions": reactions,
            "quote": quote,
            "source": {"adapter": "transcript", "line": row.line, "label": label},
        })
    fmt = {"origin": origin or "", "speakers": "listed" if speakers else "inferred"}
    # A transcript chatcom wrote from screenshots keeps that as its source.
    adapter = "screenshots" if origin == "screenshots" else "transcript"
    for m in msgs:
        m["source"]["adapter"] = adapter
    return ParseResult(msgs, 0, unparsed, len(rows), fmt, None, masked, adapter)


# ---- write ------------------------------------------------------------------------

def _esc_line(s: str) -> str:
    """A text line as a continuation: escaped when it could read as structure."""
    return "\\" + s if s.startswith(_STRUCTURE) or _split_name(s) or _STAMP.match(s) else s


def write(messages: list[dict], tz_name: str, *, source: str = "paste") -> str:
    """Messages as a transcript that parse() reads back unchanged. Raises
    ValueError for what the format cannot hold (a name that is not a valid
    speaker name, or an undated row after a dated one)."""
    if not _ORIGIN.match(source):
        raise ValueError(f"bad source {source!r}")
    tz = ZoneInfo(tz_name)
    labels: list[str] = []
    for m in messages:
        label = m["source"].get("label") or m["sender"]
        if not valid_name(label):
            raise ValueError(f"not a speaker name: {label!r}")
        q = m.get("quote") or {}
        for n in (label, q.get("sender")):
            if n is None:
                continue
            if not valid_name(n):
                raise ValueError(f"not a speaker name: {n!r}")
            if n not in labels:
                labels.append(n)
    out = [f"# source: {source}"]
    if labels:
        out.append("# speakers: " + ", ".join(_esc_item(n) for n in labels))
    current: date | None = None
    for m in messages:
        label = m["source"].get("label") or m["sender"]
        precision = m.get("time_precision", "minute")
        when = datetime.fromisoformat(m["time"]).astimezone(tz)
        if precision == "order" and current is not None:
            raise ValueError("an undated row cannot follow a dated one")
        if precision == "day" and when.date() != current:
            out.append(f"--- {when.date().isoformat()} ---")
        if precision != "order":
            current = when.date()
        q = m.get("quote")
        if q:
            qlines = q["text"].split("\n")
            first = qlines[0]
            if q.get("sender"):
                if not valid_name(q["sender"]):
                    raise ValueError(f"not a speaker name: {q['sender']!r}")
                out.append(f"> {q['sender']}: {first}")
            else:
                out.append("> " + ("\\" + first if first.startswith(("\\", " ")) or _split_name(first) else first))
            out += [">  " + ("\\" + ql if ql.startswith(("\\", " ")) else ql) for ql in qlines[1:]]
        text_lines = m["text"].split("\n") if m["text"] else [""]
        if m.get("deleted"):
            first = "<deleted>"
        elif m.get("media"):
            first = f"<{m['media']}>" + (f" {text_lines[0]}" if text_lines[0] else "")
        else:
            first = text_lines[0]
            if first.startswith("\\") or _PLACEHOLDER.match(first):
                first = "\\" + first
        rows = [first] + (["  " + (_esc_line(t) if t else "") for t in text_lines[1:]]
                          if not m.get("deleted") else [])
        if m.get("reactions"):
            rows[-1] += " " + _write_reactions(m["reactions"])
        elif _REACTIONS.search(rows[-1]):
            rows[-1] += f" (reactions: {NO_REACTIONS})"
        prefix = ""
        if precision in TIMED:
            fmt = "%Y-%m-%d %H:%M:%S" if precision == "exact" else "%Y-%m-%d %H:%M"
            prefix = f"[{when.strftime(fmt)}] "
        out.append(f"{prefix}{label}: {rows[0]}")
        out += rows[1:]
    return "\n".join(out) + "\n"


# ---- registry hooks ---------------------------------------------------------------

def _decode(data: bytes, name: str) -> str:
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            return data.decode("utf-16")
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{name} is not UTF-8 text",
                           "Save the transcript as UTF-8 text (docs/transcript-format.md).") from None


def load(paths: list[Path], ctx) -> tuple[ParseResult, str]:
    if len(paths) != 1 or not paths[0].is_file():
        raise ChatcomError("CHATCOM_E_USAGE", "a transcript is one text file",
                           "Pass one .txt file with --export (docs/transcript-format.md).")
    p = paths[0]
    with open(p, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)  # read at most the limit, even if the file grows
    if len(data) > MAX_BYTES:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{p.name} is over the {MAX_BYTES} byte limit",
                           "Split the transcript and import the newer part.", context={"path": str(p)})
    text = _decode(data, p.name)
    # Undated rows are anchored to the day the file was saved.
    anchor = datetime.fromtimestamp(p.stat().st_mtime).date()
    return parse(text, ctx.tz, aliases=ctx.aliases, key=ctx.key, anchor=anchor), p.stem
