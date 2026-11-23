"""The message schema's pure helpers: display text, identity text and the
keyed message ids.

Every id, cursor signature and phone label is an HMAC-SHA256 keyed by the
show's secret, so a phone number cannot be brute-forced back from an id and two
shows give different ids for the same message. Identity text is normalized
harder than display text (case-folded, no punctuation or spaces, every emoji
one class), so OCR noise in a screenshot still gives the same id."""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata
from datetime import datetime, timezone

# Bidi and zero-width marks that WhatsApp sprinkles into exports.
INVISIBLE = re.compile("[​‌‍‎‏‪-‮⁦-⁩﻿]")
_SPACES = re.compile(r"[ \t   ]+")

PRECISIONS = ("exact", "minute", "day", "order")
TIMED = ("exact", "minute")

# Used only where no show exists yet (unit tests, a parse outside a show).
# App code always passes the show's own key.
UNKEYED = b"chatcom-unkeyed"


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = INVISIBLE.sub("", text)
    lines = [_SPACES.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines).strip()


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return (unicodedata.category(ch) == "So" or 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF
            or cp in (0x200D, 0xFE0F) or 0x1F3FB <= cp <= 0x1F3FF)


def identity_text(text: str) -> str:
    """The text an id is built from: NFC, case-folded, letters and digits
    only, every run of emoji one token. "K" and "k", "lol!" and "lol", and
    "😂😂" and "🤣" give the same identity."""
    out, in_emoji = [], False
    for ch in unicodedata.normalize("NFC", INVISIBLE.sub("", text)).casefold():
        if _is_emoji(ch):
            if not in_emoji:
                out.append("")
            in_emoji = True
            continue
        in_emoji = False
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def keyed(key: bytes | None, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return hmac.new(key or UNKEYED, raw, hashlib.sha256).hexdigest()[:16]


def utc_minute(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def body_token(text: str, media: str | None, deleted: bool) -> str:
    """Media and deleted placeholders are fixed tokens, so the same chat
    exported from iOS and Android (different placeholder wording) agrees."""
    if deleted:
        return "<deleted>"
    if media and not text:
        return "<media>"
    return identity_text(text) or text


def message_hash(sender: str, minute: str, ordinal: int, text: str, media: str | None, deleted: bool,
                 key: bytes | None = None) -> str:
    """Id of an `exact` or `minute` row: (label, UTC minute, ordinal within
    label-minute, identity text)."""
    return keyed(key, "m", sender, minute, str(ordinal), body_token(text, media, deleted))


def context_hash(label: str, day: str | None, text: str, media: str | None, deleted: bool,
                 prev_label: str, prev_text: str, run_ordinal: int, key: bytes | None = None) -> str:
    """Id of a `day` or `order` row: it depends on the row and the one before
    it, not on where an import starts, so two overlapping imports agree."""
    return keyed(key, "c", label, day or "none", body_token(text, media, deleted), prev_label,
                 prev_text, str(run_ordinal))


def row_signature(label: str, text: str, media: str | None, deleted: bool, key: bytes | None = None) -> str:
    """One row of a cursor signature: (label, identity text)."""
    return keyed(key, "s", label, body_token(text, media, deleted))
