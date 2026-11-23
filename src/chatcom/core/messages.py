"""The message schema's pure helpers: text normalization and the content hash."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone

# Bidi and zero-width marks that WhatsApp sprinkles into exports.
INVISIBLE = re.compile("[​‌‍‎‏‪-‮⁦-⁩﻿]")
_SPACES = re.compile(r"[ \t   ]+")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = INVISIBLE.sub("", text)
    lines = [_SPACES.sub(" ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines).strip()


def utc_minute(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def message_hash(sender: str, minute: str, ordinal: int, text: str, media: str | None, deleted: bool) -> str:
    """(sender, UTC minute, ordinal within sender-minute, normalized text).
    Media and deleted placeholders hash as fixed tokens, so the same chat
    exported from iOS and Android (different placeholder wording) agrees."""
    if deleted:
        body = "<deleted>"
    elif media and not text:
        body = "<media>"
    else:
        body = text
    raw = "\x1f".join([sender, minute, str(ordinal), body]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]
