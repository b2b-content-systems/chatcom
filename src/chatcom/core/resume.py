"""Where the next episode starts in a new import. One pure function, so every
source and precision resumes by the same rules:

    1. the cursor's message id;
    2. time, in UTC, when both sides have exact or minute times; messages in
       the cursor's own minute are kept unless they match its signature;
    3. content alignment on the cursor's last rows (identity hashes, the
       match must be unique: 3 rows, then 5, then 5 with one row allowed to
       differ);
    4. nothing matched: the whole import, with a CHATCOM_W_WHOLE_IMPORT warning.

The cursor holds two signatures: raw labels (same source) and canonical names
after aliases (across sources). No text or number is stored, only keyed
hashes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chatcom.core.messages import TIMED, row_signature, utc_minute

SIG_ROWS = 5
WHOLE_IMPORT = "CHATCOM_W_WHOLE_IMPORT"


@dataclass
class Resume:
    rows: list[dict]
    method: str          # first | id | time | align | whole
    warning: dict | None


def _t(m: dict) -> datetime:
    return datetime.fromisoformat(m["time"])


def _label(m: dict, canonical: bool) -> str:
    label = m["sender"] if canonical else m["source"].get("label", m["sender"])
    # A row whose speaker is still an unnamed "Speaker X" leaves the label out.
    return "" if label.startswith("Speaker ") and len(label) <= 10 else label


def row_hash(m: dict, key: bytes | None, canonical: bool) -> str:
    return row_signature(_label(m, canonical), m["text"], m["media"], m["deleted"], key)


def signature(rows: list[dict], key: bytes | None, canonical: bool) -> list[str]:
    return [row_hash(m, key, canonical) for m in rows[-SIG_ROWS:]]


def make_cursor(read: list[dict], adapter: str, key: bytes | None) -> dict:
    """The cursor after the last row read, excluded or not."""
    last = read[-1]
    precision = last.get("time_precision", "minute")
    return {"minute": utc_minute(_t(last)) if precision in TIMED else None,
            "hash": last["id"], "adapter": adapter, "precision": precision,
            "signature": {"raw": signature(read, key, False), "canonical": signature(read, key, True)}}


def _align(rows: list[dict], sig: list[str], key: bytes | None, canonical: bool) -> int | None:
    """Index just after the unique place the signature ends, or None."""
    if not sig:
        return None
    hashes = [row_hash(m, key, canonical) for m in rows]
    for k, tolerance in ((3, 0), (5, 0), (5, 1)):
        if len(sig) < k or len(hashes) < k:
            continue
        pat = sig[-k:]
        hits = [end for end in range(k - 1, len(hashes))
                if sum(a != b for a, b in zip(hashes[end - k + 1:end + 1], pat)) <= tolerance]
        if len(hits) == 1:
            return hits[0] + 1
    return None


def resume_point(cursor: dict | None, rows: list[dict], key: bytes | None = None,
                 adapter: str | None = None) -> Resume:
    if cursor is None:
        return Resume(rows, "first", None)
    for i, m in enumerate(rows):
        if m["id"] == cursor.get("hash"):
            return Resume(rows[i + 1:], "id", None)
    sigs = cursor.get("signature") or {}
    if (cursor.get("precision", "minute") in TIMED and cursor.get("minute") and rows
            and all(m.get("time_precision", "minute") in TIMED for m in rows)):
        seen = set(sigs.get("canonical", []))
        # A cursor upgraded from v1 has no signature: keep the v1 rule and
        # drop the whole cursor minute rather than repeat part of it.
        whole_minute = not seen
        out = []
        for m in rows:
            minute = utc_minute(_t(m))
            if minute < cursor["minute"]:
                continue
            if minute == cursor["minute"] and (whole_minute or row_hash(m, key, True) in seen):
                continue
            out.append(m)
        return Resume(out, "time", None)
    same = adapter is not None and adapter == cursor.get("adapter")
    for canonical in ((False, True) if same else (True,)):
        idx = _align(rows, sigs.get("canonical" if canonical else "raw", []), key, canonical)
        if idx is not None:
            return Resume(rows[idx:], "align", None)
    warning = None
    if rows:
        warning = {"code": WHOLE_IMPORT,
                   "what": "this import does not overlap the last episode; all of it is used, so earlier "
                           "messages may repeat",
                   "fix": "Pass --since DATE to start later, or import screenshots that overlap last week's.",
                   "from": rows[0]["time"], "to": rows[-1]["time"]}
    return Resume(rows, "whole", warning)
