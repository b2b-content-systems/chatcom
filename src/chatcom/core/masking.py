"""Phone-number masking and sender aliases, shared by every adapter.

Phone numbers never leave in message text or as a sender. A number with an
alias becomes that name; any other becomes its stable `Unnamed xxxxxx` label,
an HMAC keyed by the show's secret, so the label cannot be reversed to the
number. Fails closed: see mask_numbers."""
from __future__ import annotations

import hashlib
import hmac
import re
import unicodedata

from chatcom.core.messages import UNKEYED, normalize_text

_PHONE = re.compile(r"^\+?[\d\s()\-‑.]{7,}$")
# A phone number inside message text: 10+ digits with phone separators only
# (dates and times have fewer digits or other separators).
# A run of digits joined by anything a phone number is written with. The scan
# runs on a copy of the text where every Unicode space is " " and every dash,
# minus or middle dot is "-", so exotic separators cannot hide a number.
_DIGIT_RUN = re.compile(r"\+?\d(?:[\d ()\-./]*\d)?")
MIN_MASK_DIGITS = 9     # Spanish and Portuguese mobiles have 9
SHORT_PHONE_DIGITS = 7  # 7-8 digit local numbers: masked when grouped or after a phone word
MAX_PHONE_DIGITS = 15   # E.164; a longer run outside a link is masked whole
_DATE_LIKE = re.compile(r"^(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[./-]\d{1,2}[./-](\d{4}|\d{2}))( \d{1,2})?$")
_DECIMAL = re.compile(r"^[1-9]\d?\.\d{1,6}$")
_YEAR_RANGE = re.compile(r"^(19|20)\d\d ?- ?(19|20)\d\d$")
_GROUPED = re.compile(r"\d[ -]\d")
_PHONE_WORD = re.compile(r"(tel|telefon|telephone|téléphone|phone|call|cell|mobile|handy|ruf|liga|llama|"
                         r"appelle|número|numero|nummer|nº|no\.|durchwahl|fax|whatsapp|zap)\W{0,3}$", re.I)
_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)\S+", re.I)
_PCT_PLUS = re.compile(r"%2B", re.I)


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_key(s: str) -> str:
    # The last 10 digits: the same number written with or without its
    # country code (+1 555 010 2233, 555.010.2233) is one person.
    return _digits(s)[-10:]


def alias_map(aliases: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for canon, others in aliases.items():
        for o in [canon, *others]:
            o = normalize_text(o)
            out[o.casefold()] = canon
            if _PHONE.match(o):
                out["#" + _phone_key(o)] = canon
                out["#9" + _digits(o)[-9:]] = canon  # a national number without its country code
    return out


def phone_label(number: str, key: bytes | None = None) -> str:
    """A stable stand-in for a phone number: the same number gets the same
    label in every export of one show, whatever order people appear in."""
    return "Unnamed " + hmac.new(key or UNKEYED, _phone_key(number).encode(), hashlib.sha256).hexdigest()[:6]


def is_phone(label: str) -> bool:
    return bool(_PHONE.match(label))


def canonical(sender: str, amap: dict[str, str], masked: dict[str, str], key: bytes | None = None) -> str:
    """The sender's name after aliases; a bare phone number becomes its label."""
    if sender.casefold() in amap:
        return amap[sender.casefold()]
    if _PHONE.match(sender):
        k = "#" + _phone_key(sender)
        if k in amap:
            return amap[k]
        masked.setdefault(sender, phone_label(sender, key))
        return masked[sender]
    return sender


def _scan_copy(text: str) -> str:
    """Same length as text: Unicode spaces become " ", dashes, minus signs and
    middle dots become "-", so the digit scan sees every separator."""
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Zs" or ch == "\t":
            out.append(" ")
        elif cat == "Pd" or ch in "−·‧・":
            out.append("-")
        else:
            out.append(ch)
    return "".join(out)


def mask_numbers(text: str, alias_map: dict[str, str], key: bytes | None = None) -> str:
    """Phone numbers never leave in message text. A number with an alias
    becomes that name; any other becomes its stable 'Unnamed' label (a WhatsApp
    @mention of an unsaved contact is written as their number, so it maps to
    the same label as their messages). Fails closed: a run of 9 or more digits
    is masked unless it is plainly a date or a short decimal; a 7-8 digit run is
    masked when grouped like a phone or after a phone word; inside a link,
    every phone-like run is masked and only ids over 15 digits stay."""
    if not text:
        return text
    scan = _scan_copy(text)
    links = [(m.start(), m.end()) for m in _URL.finditer(scan)]
    pieces, pos = [], 0
    for m in _DIGIT_RUN.finditer(scan):
        start, end = m.start(), m.end()
        number = scan[start:end]
        digits = _digits(number)
        in_link = any(a <= start < b for a, b in links)
        plus = number.startswith("+") or (start >= 3 and bool(_PCT_PLUS.fullmatch(scan[start - 3:start])))
        if in_link:
            mask = (plus and len(digits) >= SHORT_PHONE_DIGITS) or MIN_MASK_DIGITS <= len(digits) <= MAX_PHONE_DIGITS
        elif _DATE_LIKE.match(number) and not plus:
            mask = False  # 2026-01-15, 15.01.2026, 15/01/26 (and an hour after it)
        elif len(digits) >= MIN_MASK_DIGITS:
            spare = _DATE_LIKE.match(number) or (_DECIMAL.match(number) and len(digits) < MIN_MASK_DIGITS)
            mask = not spare
        elif len(digits) >= SHORT_PHONE_DIGITS:
            grouped = bool(_GROUPED.search(number)) and not _YEAR_RANGE.match(number) and "." not in number
            mask = grouped or bool(_PHONE_WORD.search(scan[max(0, start - 16):start]))
        else:
            mask = False
        if not mask:
            continue
        label = None
        if len(digits) <= MAX_PHONE_DIGITS:
            label = alias_map.get("#" + _phone_key(number)) or alias_map.get("#9" + digits[-9:])
        pieces.append(text[pos:start])
        pieces.append(label or phone_label(number, key))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)
