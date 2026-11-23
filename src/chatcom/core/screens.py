"""Join screenshot reads into one chat, with fixed rules (no model involved).

    reads, one per copy, in manifest order
      -> order      reversed when the visible times or the seams say the images
                    are newest first (--reverse / --no-reverse force it)
      -> seams      rows repeated across two neighbouring images are dropped: a
                    run of 2 or more matching rows, or 1 when either copy is cut
                    off (partial) or both show the same time; a cut-off copy is
                    replaced by the whole one
      -> speakers   right side = the operator (or --speaker right=NAME); a left
                    bubble with no name takes the name above it, across seams;
                    no name at all = "Speaker A"; --speaker LABEL=NAME renames
      -> dates      day separators ("Today", "Monday", "Jan 9") resolved
                    against the image's capture date (--anchor-date overrides)
      -> messages   ready for the transcript writer (`# source: screenshots`)

Rows before the first date the screenshots show have no date (precision
`order`); a row with a date and a time is `minute`; with a date only, `day`."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo

from chatcom.core.errors import ChatcomError
from chatcom.core.messages import identity_text, normalize_text

OPERATOR_SIDE = "right"
UNNAMED = "Speaker A"
BACKWARD_SLACK = timedelta(minutes=5)  # a misread digit or an edited message
MIDNIGHT_GAP = timedelta(hours=6)       # a bigger jump back with no header is the next day
MAX_NAME = 40
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_TIME = re.compile(r"^\s*(\d{1,2})[:.](\d{2})(?::\d{2})?\s*([aApP]\.?\s?[mM]\.?)?\s*$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_MON_DAY = re.compile(r"^([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?$")
_DAY_MON = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\.?\s+([a-z]{3,9})\.?(?:,?\s+(\d{4}))?$")
_NUMERIC = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


@dataclass
class Image:
    name: str            # copy file name, for messages
    captured: date       # the capture date (EXIF, else the file time)
    rows: list[dict]
    chat_name: str | None = None


@dataclass
class Joined:
    messages: list[dict]
    chat_name: str | None
    reversed: bool
    seam_drops: int
    labels: list[str] = field(default_factory=list)


# ---- small parsers ---------------------------------------------------------------

def parse_time(s: str | None) -> tuple[int, int] | None:
    m = _TIME.match(s or "")
    if not m:
        return None
    h, mi = int(m[1]), int(m[2])
    if m[3]:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if m[3].lower().startswith("p") else 0)
    return (h, mi) if h < 24 and mi < 60 else None


def _month(word: str) -> int | None:
    w = word.lower()[:3]
    return MONTHS.index(w) + 1 if w in MONTHS else None


def _safe(y: int, mo: int, d: int) -> date | None:
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def resolve_date(label: str, ref: date) -> date | None:
    """A day separator as the app shows it, against the capture date `ref`.
    A date with no year is the latest one on or before `ref`."""
    s = normalize_text(label).lower().strip(" .,")
    if s == "today":
        return ref
    if s == "yesterday":
        return ref - timedelta(days=1)
    if s in WEEKDAYS:
        back = (ref.weekday() - WEEKDAYS.index(s)) % 7
        return ref - timedelta(days=back)
    for rx, order in ((_ISO, "ymd"), (_MON_DAY, "md"), (_DAY_MON, "dm"), (_NUMERIC, "num")):
        m = rx.match(s)
        if not m:
            continue
        if order == "ymd":
            return _safe(int(m[1]), int(m[2]), int(m[3]))
        if order == "num":
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
            return _safe(y, int(m[1]), int(m[2])) or _safe(y, int(m[2]), int(m[1]))
        mo = _month(m[1] if order == "md" else m[2])
        d = int(m[2] if order == "md" else m[1])
        if mo is None:
            return None
        if m[3]:
            return _safe(int(m[3]), mo, d)
        got = _safe(ref.year, mo, d)
        if got and got > ref:
            got = _safe(ref.year - 1, mo, d)
        return got
    return None


def clean_name(name: str | None) -> str | None:
    """A visible sender name as a transcript speaker name, or None."""
    if not name:
        return None
    n = normalize_text(name.replace("\n", " ")).lstrip("~").strip().replace(": ", " ")
    n = n.lstrip("\\#>-[<").strip()[:MAX_NAME]
    n = normalize_text(n).rstrip(":").strip()  # a cut can leave a trailing colon or space
    if not n or n.isdigit():
        return None
    return n


# ---- matching rows across a seam ---------------------------------------------------------

def _messages(img: Image) -> list[dict]:
    return [r for r in img.rows if r.get("type", "message") == "message"]


def _same(a: dict, b: dict) -> bool:
    if a.get("side") != b.get("side"):
        return False
    na, nb = clean_name(a.get("name")), clean_name(b.get("name"))
    if na and nb and na != nb:
        return False
    if (a.get("placeholder") or None) != (b.get("placeholder") or None):
        return False
    ta, tb = identity_text(a.get("text", "")), identity_text(b.get("text", ""))
    if a.get("partial") or b.get("partial"):
        short, long_ = sorted((ta, tb), key=len)
        return (len(short) >= 3 or short == long_) and (long_.startswith(short) or long_.endswith(short))
    return ta == tb


def overlap(a: Image, b: Image) -> int:
    """How many of b's first message rows repeat a's last ones (0 when the
    match is too weak to trust)."""
    ra, rb = _messages(a), _messages(b)
    for k in range(min(len(ra), len(rb)), 0, -1):
        if all(_same(x, y) for x, y in zip(ra[-k:], rb[:k])):
            if k >= 2:
                return k
            x, y = ra[-1], rb[0]
            same_time = x.get("time") and x.get("time") == y.get("time")
            if x.get("partial") or y.get("partial") or same_time:
                return 1
    return 0


def merge_halves(a: Image, b: Image) -> Image:
    """The top and bottom halves of one split image as one image again: the
    rows b repeats from a are dropped, so join() never sees two screenshots."""
    k = overlap(a, b)
    head = {id(r) for r in _messages(b)[:k]}
    return Image(a.name, a.captured, list(a.rows) + [r for r in b.rows if id(r) not in head],
                 a.chat_name or b.chat_name)


def _times(img: Image) -> list[int]:
    out = []
    for r in _messages(img):
        t = parse_time(r.get("time"))
        if t:
            out.append(t[0] * 60 + t[1])
    return out


def is_reversed(images: list[Image]) -> bool:
    """Newest first? From seam overlaps, then visible times."""
    fwd = back = 0
    for a, b in zip(images, images[1:]):
        f, r = overlap(a, b), overlap(b, a)
        fwd += f > 0
        back += r > 0 and f == 0
    if fwd != back:
        return back > fwd
    for a, b in zip(images, images[1:]):
        ta, tb = _times(a), _times(b)
        if ta and tb and a.captured == b.captured:
            fwd += tb[0] > ta[-1]
            back += tb[-1] < ta[0]
    return back > fwd


# ---- join ----------------------------------------------------------------------------------

def join(images: list[Image], *, tz: tzinfo, operator: str, speakers: dict[str, str] | None = None,
         reverse: bool | None = None, anchor: date | None = None) -> Joined:
    speakers = dict(speakers or {})
    for k, v in list(speakers.items()):
        clean = clean_name(v)
        if clean is None:
            raise ChatcomError("CHATCOM_E_USAGE", f"--speaker {k}={v!r}: not a name chatcom can use",
                               "Use a name without a line break, not all digits (a colon is dropped).")
        speakers[k] = clean
    rev = is_reversed(images) if reverse is None else reverse
    # copies of the rows: the caller's reads are never changed
    order = [Image(img.name, img.captured, [dict(r) for r in img.rows], img.chat_name)
             for img in (reversed(images) if rev else images)]

    # seams: drop b's repeated head; keep the whole copy of a cut-off row
    drops = 0
    linked = [True]  # does each image overlap the one before it?
    for a, b in zip(order, order[1:]):
        k = overlap(a, b)
        linked.append(k > 0)
        if not k:
            continue
        head = _messages(b)[:k]
        for p, h in zip(_messages(a)[-k:], head):
            if p.get("partial") and not h.get("partial"):
                p.clear()
                p.update(h)
        drop_ids = {id(r) for r in head}
        b.rows = [r for r in b.rows if id(r) not in drop_ids]
        drops += k
    kept = [img.rows for img in order]

    right = speakers.pop(OPERATOR_SIDE, None) or operator
    messages: list[dict] = []
    seen_labels: list[str] = []
    current: date | None = anchor
    carry: str | None = None
    chat_name = next((img.chat_name for img in order if img.chat_name), None)
    last_timed: datetime | None = None
    header_since = False
    for i, (img, rows) in enumerate(zip(order, kept)):
        ref = img.captured  # "Today" is the day each screenshot was taken; --anchor-date only dates the start
        if not linked[i]:
            carry = None  # a gap between screenshots: a name above the gap is not this bubble's sender
        for r in rows:
            if r.get("type") == "date":
                current = resolve_date(r.get("text", ""), ref) or current
                header_since = True
                continue
            if r.get("side") == OPERATOR_SIDE:
                label = right
            else:
                label = clean_name(r.get("name")) or carry or UNNAMED
                carry = label
            if label not in seen_labels:
                seen_labels.append(label)
            hm = parse_time(r.get("time"))
            if current and hm:
                when = datetime.combine(current, time(*hm)).replace(tzinfo=tz)
                if last_timed and when < last_timed - BACKWARD_SLACK:
                    if not header_since and last_timed - when > MIDNIGHT_GAP:
                        current += timedelta(days=1)  # past midnight with no day header in view
                        when += timedelta(days=1)
                    else:
                        raise ChatcomError(
                            "CHATCOM_E_USAGE",
                            f"the screenshot times go back from {last_timed:%Y-%m-%d %H:%M} to {when:%H:%M} "
                            f"in {img.name}, so the screenshots are out of order",
                            "Check the order: pass --reverse (newest first) or --no-reverse, or set "
                            "--anchor-date. Otherwise type the chat as a transcript (docs/transcript-format.md).",
                            context={"image": img.name})
                last_timed, header_since = when, False
                precision = "minute"
            elif current:
                when = datetime.combine(current, time(12)).replace(tzinfo=tz)
                precision = "day"
            else:
                when = datetime.combine(ref, time(12)).replace(tzinfo=tz)
                precision = "order"
            ph = r.get("placeholder")
            q = r.get("quote")
            messages.append({
                "label": label,
                "time": when.isoformat(timespec="seconds"),
                "time_precision": precision,
                "text": normalize_text(r.get("text", "")) if ph != "deleted" else "",
                "media": ph if ph and ph != "deleted" else None,
                "deleted": ph == "deleted",
                "reactions": [{"emoji": x["emoji"], "sender": clean_name(x.get("sender")), "count": x["count"]}
                              for x in r.get("reactions") or []] or None,
                "quote": ({"sender": clean_name(q.get("sender")), "text": normalize_text(q.get("text", ""))}
                          if q else None),
                "image": img.name,
            })

    unknown = [k for k in speakers if k not in seen_labels and f"Speaker {k}" not in seen_labels]
    if unknown:
        raise ChatcomError("CHATCOM_E_SPEAKER_UNMAPPED",
                           f"--speaker names {', '.join(unknown)}, which is not a label in these screenshots",
                           f"Use one of: {', '.join(seen_labels) or 'none'} (or right=NAME for your own bubbles).",
                           context={"labels": seen_labels, "unknown": unknown})
    rename = {(k if k in seen_labels else f"Speaker {k}"): v for k, v in speakers.items()}
    for m in messages:
        m["label"] = rename.get(m["label"], m["label"])
        for x in m["reactions"] or []:
            if x["sender"] in rename:
                x["sender"] = rename[x["sender"]]
        if m["quote"] and m["quote"]["sender"] in rename:
            m["quote"]["sender"] = rename[m["quote"]["sender"]]
    labels = [rename.get(x, x) for x in seen_labels]
    return Joined(messages, chat_name, rev, drops, labels)
