"""WhatsApp export adapter: iOS and Android .txt exports, and the .zip that
holds one. Standard library only (whatstk is GPL-3.0 and cannot ship in an
MIT package).

What it handles:
- iOS   `[15/01/2026, 21:41:05] Ava: text`   and   `[1/15/26, 9:41:05 PM] Ava: text`
- Android `15/01/2026, 21:41 - Ava: text`    and   `1/15/26, 9:41 PM - Ava: text`
- dots, slashes or dashes in dates; 2- or 4-digit years; year-first dates;
  12- and 24-hour clocks, including `p. m.` and the narrow no-break space
  newer exports put before AM/PM; no comma between date and time (pt-BR iOS)
- day/month order inferred over the whole file
- multi-line messages, system lines (joins, subject changes, the encryption
  notice), media and deleted placeholders in English, German, Spanish,
  Portuguese and French, the "edited" marker, invisible direction marks
- local times resolved in the show's zone, including the repeated hour at a
  DST fall-back

Every parse reports its unparsed-line rate; the caller fails above 2%.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.core.errors import ChatcomError
from chatcom.core.messages import message_hash, normalize_text, utc_minute

MAX_TXT_BYTES = 256 * 1024 * 1024

_DATE = r"(?P<d1>\d{1,4})[./-](?P<d2>\d{1,2})[./-](?P<d3>\d{1,4})\.?"
_AMPM = r"(?P<ampm>[aApP]\.?\s?[mM]\.?)"
_TIME = r"(?P<h>\d{1,2})[:.](?P<mi>\d{2})(?:[:.](?P<s>\d{2}))?(?:\s?" + _AMPM + r")?"
IOS_HEADER = re.compile(r"^\[" + _DATE + r",?\s" + _TIME + r"\]\s(?P<rest>.*)$", re.S)
ANDROID_HEADER = re.compile(r"^" + _DATE + r",?\s" + _TIME + r"\s[-–]\s(?P<rest>.*)$", re.S)
LOOKS_LIKE_HEADER = re.compile(r"^\[?\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")

# Placeholders, matched on lower-cased normalized text.
_MEDIA_WORDS = {
    "image": ("image omitted", "bild weggelassen", "imagen omitida", "imagem ocultada", "image absente", "photo omitted"),
    "video": ("video omitted", "video weggelassen", "video omitido", "vídeo omitido", "vídeo ocultado", "vidéo absente"),
    "audio": ("audio omitted", "audio weggelassen", "audio omitido", "áudio ocultado", "audio omis"),
    "sticker": ("sticker omitted", "sticker weggelassen", "sticker omitido", "figurinha omitida", "autocollant omis"),
    "gif": ("gif omitted", "gif weggelassen", "gif omitido", "gif ocultado"),
    "document": ("document omitted", "dokument weggelassen", "documento omitido", "documento ocultado", "document omis"),
    "contact": ("contact card omitted", "kontaktkarte weggelassen", "tarjeta de contacto omitida",
                "cartão de contato omitido", "fiche contact omise"),
}
_MEDIA_GENERIC = ("<media omitted>", "<medien ausgeschlossen>", "<multimedia omitido>", "<mídia oculta>",
                  "<médias omis>", "<media weggelassen>")
_EXT_KIND = {
    "jpg": "image", "jpeg": "image", "png": "image", "heic": "image", "webp": "sticker", "gif": "gif",
    "mp4": "video", "mov": "video", "3gp": "video", "opus": "audio", "m4a": "audio", "mp3": "audio",
    "aac": "audio", "ogg": "audio", "pdf": "document", "doc": "document", "docx": "document",
    "xlsx": "document", "pptx": "document", "txt": "document", "vcf": "contact",
}
_ATTACHED = re.compile(r"^<attached: [^>]*\.([A-Za-z0-9]{2,5})>\s*", re.I)
_FILE_ATTACHED = re.compile(r"^\S+\.([A-Za-z0-9]{2,5}) \((?:file attached|datei angehängt|archivo adjunto|arquivo anexado|fichier joint)\)\s*", re.I)
_LOCATION = re.compile(r"^(location|standort|ubicación|localização|position|live location shared)[^:]*:", re.I)
_POLL = re.compile(r"^(poll|umfrage|encuesta|enquete|sondage):\s*", re.I)
_DELETED = ("this message was deleted", "you deleted this message", "diese nachricht wurde gelöscht",
            "du hast diese nachricht gelöscht", "se eliminó este mensaje", "eliminaste este mensaje",
            "este mensaje fue eliminado", "mensagem apagada", "você apagou esta mensagem",
            "esta mensagem foi apagada", "ce message a été supprimé", "vous avez supprimé ce message")
_EDITED = re.compile(r"\s*<(this message was edited|nachricht wurde bearbeitet|se editó este mensaje\.?|"
                     r"mensagem editada|ce message a été modifié)>\s*$", re.I)
_SYSTEM_PHRASES = re.compile(
    r"( added | removed | left$| joined | created group| created this group| changed the subject|"
    r"changed this group|changed the group| changed their phone number| deleted this group|"
    r"pinned a message| hat .* hinzugefügt| hat die gruppe| hat den betreff| ist beigetreten|"
    r" añadió | eliminó a | salió$| cambió el asunto| creó el grupo| se unió| adicionou | removeu |"
    r" saiu$| mudou o assunto| criou o grupo| entrou usando| a ajouté | est parti| a changé le sujet| a créé le groupe)",
    re.I)
_ENCRYPTION = ("end-to-end encrypted", "ende-zu-ende-verschlüsselt", "cifrados de extremo a extremo",
               "protegidas com a criptografia de ponta a ponta", "chiffrés de bout en bout")
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


@dataclass
class _Raw:
    line: int
    d: tuple[int, int, int]
    t: tuple[int, int, int, str | None]
    rest: str
    extra: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    messages: list[dict]
    system_lines: int
    unparsed: list[tuple[int, str]]
    header_lines: int
    fmt: dict
    group_name: str | None
    masked: dict[str, str]

    @property
    def unparsed_rate(self) -> float:
        total = self.header_lines + len(self.unparsed)
        return len(self.unparsed) / total if total else 1.0

    def senders(self) -> Counter:
        return Counter(m["sender"] for m in self.messages)


# ---- reading the export --------------------------------------------------

def read_export(path: str | Path) -> tuple[str, str]:
    """(text, display name for the default slug). Reads a .txt, or the single
    chat .txt inside a .zip without unpacking media."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ChatcomError("CHATCOM_E_EXPORT_NOT_FOUND", f"no file at {p}",
                           "Check the path. In WhatsApp: open the chat, More, Export chat, Without media.",
                           context={"path": str(p)})
    if p.suffix.lower() == ".zip":
        return _read_zip(p), _display_name(p.stem)
    if p.stat().st_size > MAX_TXT_BYTES:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{p.name} is {p.stat().st_size} bytes, over the {MAX_TXT_BYTES} byte limit",
                           "Export a shorter range of the chat.", context={"path": str(p)})
    return _decode(p.read_bytes(), p.name), _display_name(p.stem)


def _read_zip(p: Path) -> str:
    try:
        zf = zipfile.ZipFile(p)
    except zipfile.BadZipFile as e:
        raise ChatcomError("CHATCOM_E_EXPORT_ZIP", f"{p.name} is not a readable zip ({e})",
                           "Re-export the chat from WhatsApp, or pass the .txt inside it.") from None
    with zf:
        txts = [i for i in zf.infolist() if i.filename.lower().endswith(".txt") and not i.is_dir()
                and not Path(i.filename).name.startswith("._")]
        if len(txts) > 1:
            pref = [i for i in txts if Path(i.filename).name == "_chat.txt"
                    or Path(i.filename).name.lower().startswith("whatsapp chat")]
            txts = pref if len(pref) == 1 else txts
        if len(txts) != 1:
            names = [i.filename for i in txts][:5]
            raise ChatcomError("CHATCOM_E_EXPORT_ZIP",
                               f"{p.name} holds {len(txts)} chat text files {names}; expected exactly one",
                               "Pass the chat's .txt file directly.", context={"txt_files": names})
        info = txts[0]
        if info.file_size > MAX_TXT_BYTES:
            raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE",
                               f"{info.filename} unpacks to {info.file_size} bytes, over the {MAX_TXT_BYTES} byte limit",
                               "Export a shorter range of the chat.")
        try:
            with zf.open(info) as fh:
                data = fh.read(MAX_TXT_BYTES + 1)
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError) as e:
            # encrypted, corrupt (CRC) or an unsupported compression method
            raise ChatcomError("CHATCOM_E_EXPORT_ZIP", f"cannot read {info.filename} inside {p.name} ({type(e).__name__})",
                               "Re-export the chat from WhatsApp, or pass the .txt inside it.") from None
        return _decode(data, info.filename)


def _decode(data: bytes, name: str) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{name} is not valid UTF-16 text",
                               "Pass the WhatsApp export unchanged.") from None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{name} is not UTF-8 text",
                           "Pass the WhatsApp export unchanged; do not re-save it in another encoding.") from None


def _display_name(stem: str) -> str:
    s = re.sub(r"^whatsapp chat\s*(with|-|–|mit|con|com|avec)?\s*", "", stem.strip(), flags=re.I)
    return s or stem


# ---- parsing ---------------------------------------------------------------

_LEAD = "​‎‏‪‫‬⁦⁧⁨⁩﻿"


def _match(line: str):
    # Only leading marks are stripped: an LRM right after "Name: " is how iOS
    # marks a system line, so the rest of the line must stay as exported.
    probe = line.lstrip(_LEAD).replace(" ", " ").replace(" ", " ")
    return IOS_HEADER.match(probe), ANDROID_HEADER.match(probe), probe


def parse(text: str, tz_name: str, *, aliases: dict[str, list[str]] | None = None) -> ParseResult:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise ChatcomError("CHATCOM_E_TIMEZONE", f"unknown timezone {tz_name!r}",
                           "Pass an IANA zone with --tz, for example --tz America/New_York.") from None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    ios_hits = android_hits = 0
    for ln in lines[:2000]:
        i, a, _ = _match(ln)
        ios_hits += bool(i)
        android_hits += bool(a)
    platform = "ios" if ios_hits >= android_hits else "android"
    rx = IOS_HEADER if platform == "ios" else ANDROID_HEADER

    raws: list[_Raw] = []
    unparsed: list[tuple[int, str]] = []
    for no, ln in enumerate(lines, start=1):
        _, _, probe = _match(ln)
        m = rx.match(probe)
        if m:
            d = (int(m["d1"]), int(m["d2"]), int(m["d3"]), len(m["d1"]))
            ampm = m["ampm"]
            t = (int(m["h"]), int(m["mi"]), int(m["s"] or 0),
                 ("p" if ampm.lower().startswith("p") else "a") if ampm else None)
            raws.append(_Raw(no, d, t, m["rest"]))
            continue
        if not ln.strip():
            if raws:
                raws[-1].extra.append("")
            continue
        if LOOKS_LIKE_HEADER.match(probe) or not raws:
            unparsed.append((no, ln))
            continue
        raws[-1].extra.append(ln)

    order = _date_order(raws)
    clock = "12h" if any(r.t[3] for r in raws) else "24h"
    fmt = {"platform": platform, "date_order": order, "clock": clock}

    # first pass: naive datetimes, raw senders
    rows = []
    for r in raws:
        try:
            naive = _naive(r, order)
        except ValueError:
            unparsed.append((r.line, r.rest))
            continue
        body = "\n".join([r.rest] + r.extra).rstrip("\n")
        rows.append((r.line, naive, body))
    unparsed.sort()

    candidate = Counter()
    for _, _, body in rows:
        if ": " in body:
            candidate[normalize_text(body.split(": ", 1)[0])] += 1

    alias_map = _alias_map(aliases or {})
    masked: dict[str, str] = {}
    group_name = None
    system = 0
    msgs: list[dict] = []
    ordinals: Counter = Counter()
    prev_utc: datetime | None = None
    for line_no, naive, body in rows:
        aware = _localize(naive, tz, prev_utc)
        prev_utc = aware.astimezone(timezone.utc)
        sender, raw_text = _split(body)
        if sender is None:
            system += 1
            continue
        sender_n = normalize_text(sender)
        first = raw_text.split("\n", 1)[0]
        if platform == "ios" and first.startswith("‎") and not _placeholder(normalize_text(first)):
            system += 1
            if group_name is None:
                group_name = sender_n
            continue
        if platform == "android" and _SYSTEM_PHRASES.search(" " + sender_n) and candidate[sender_n] < 3:
            system += 1
            continue
        if any(p in normalize_text(raw_text).lower() for p in _ENCRYPTION) and len(raw_text) > 60:
            system += 1
            continue
        text, media, deleted, edited = _content(raw_text)
        text = mask_numbers(text, alias_map)
        name = _canonical(sender_n, alias_map, masked)
        minute = utc_minute(aware)
        key = (name, minute)
        ordinal = ordinals[key]
        ordinals[key] += 1
        msgs.append({
            "id": message_hash(name, minute, ordinal, text, media, deleted),
            "sender": name,
            "time": aware.isoformat(timespec="seconds"),
            "text": text,
            "media": media,
            "deleted": deleted,
            "edited": edited,
            "reply_to": None,
            "reactions": None,
            "source": {"adapter": "whatsapp", "line": line_no},
        })
    return ParseResult(msgs, system, unparsed, len(rows), fmt, group_name, masked)


def _date_order(raws: list[_Raw]) -> str:
    if not raws:
        return "dmy"
    if sum(r.d[3] == 4 for r in raws) > len(raws) / 2:
        return "ymd"

    def valid(order: str) -> int:
        n = 0
        for r in raws:
            try:
                _naive(r, order)
                n += 1
            except ValueError:
                pass
        return n
    # Read the whole file: the order under which more dates exist wins, so
    # one corrupt line cannot flip every other date.
    vd, vm = valid("dmy"), valid("mdy")
    if vd != vm:
        return "dmy" if vd > vm else "mdy"

    # every day is 12 or under: pick the reading under which time runs forward.
    def violations(order: str) -> int:
        prev, bad = None, 0
        for r in raws:
            try:
                cur = _naive(r, order)
            except ValueError:
                bad += 1
                continue
            if prev and cur < prev - timedelta(hours=2):
                bad += 1
            prev = cur
        return bad
    dmy, mdy = violations("dmy"), violations("mdy")
    if dmy != mdy:
        return "dmy" if dmy < mdy else "mdy"
    return "mdy" if any(r.t[3] for r in raws) else "dmy"


def _naive(r: _Raw, order: str) -> datetime:
    a, b, c, _ = r.d
    if order == "ymd":
        y, mo, d = a, b, c
    elif order == "mdy":
        mo, d, y = a, b, c
    else:
        d, mo, y = a, b, c
    if y < 100:
        y += 2000
    h, mi, s, ampm = r.t
    if ampm:
        if not 1 <= h <= 12:
            raise ValueError("12-hour clock out of range")
        h = h % 12 + (12 if ampm == "p" else 0)
    return datetime(y, mo, d, h, mi, s)


def _localize(naive: datetime, tz: ZoneInfo, prev_utc: datetime | None) -> datetime:
    a0 = naive.replace(tzinfo=tz, fold=0)
    a1 = naive.replace(tzinfo=tz, fold=1)
    if a0.utcoffset() == a1.utcoffset() or prev_utc is None:
        return a0
    # The repeated hour of a DST fall-back: once time has passed the first
    # pass through it, the export is in the second one.
    if a0.astimezone(timezone.utc) < prev_utc:
        return a1
    return a0


def _split(body: str) -> tuple[str | None, str]:
    if ": " not in body.split("\n", 1)[0]:
        return None, body
    sender, text = body.split(": ", 1)
    return sender, text


def _placeholder(first: str) -> bool:
    low = first.lower().rstrip(".")
    if low in _DELETED or _ATTACHED.match(first) or _LOCATION.match(first) or _POLL.match(first):
        return True
    return any(low.endswith(w) for ws in _MEDIA_WORDS.values() for w in ws) or low in _MEDIA_GENERIC


def _content(raw: str) -> tuple[str, str | None, bool, bool]:
    text = normalize_text(raw)
    edited = False
    m = _EDITED.search(text)
    if m:
        edited = True
        text = text[: m.start()].rstrip()
    first, _, rest = text.partition("\n")
    low = first.lower().rstrip(".")
    if low in _DELETED:
        return "", None, True, edited
    if low in _MEDIA_GENERIC:
        return rest.strip(), "other", False, edited
    for kind, words in _MEDIA_WORDS.items():
        if any(low.endswith(w) for w in words):
            return rest.strip(), kind, False, edited
    am = _ATTACHED.match(first) or _FILE_ATTACHED.match(first)
    if am:
        kind = _EXT_KIND.get(am.group(1).lower(), "other")
        caption = (first[am.end():] + ("\n" + rest if rest else "")).strip()
        return caption, kind, False, edited
    if _LOCATION.match(first):
        return "", "location", False, edited
    pm = _POLL.match(first)
    if pm:
        return (first[pm.end():] + ("\n" + rest if rest else "")).strip(), "poll", False, edited
    return text, None, False, edited


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _phone_key(s: str) -> str:
    # The last 10 digits: the same number written with or without its
    # country code (+1 555 010 2233, 555.010.2233) is one person.
    return _digits(s)[-10:]


def _alias_map(aliases: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for canon, others in aliases.items():
        for o in [canon, *others]:
            o = normalize_text(o)
            out[o.casefold()] = canon
            if _PHONE.match(o):
                out["#" + _phone_key(o)] = canon
                out["#9" + _digits(o)[-9:]] = canon  # a national number without its country code
    return out


def phone_label(number: str) -> str:
    """A stable stand-in for a phone number: the same number gets the same
    label in every export, whatever order people appear in."""
    return "Unnamed " + hashlib.sha256(_phone_key(number).encode()).hexdigest()[:6]


def _canonical(sender: str, alias_map: dict[str, str], masked: dict[str, str]) -> str:
    if sender.casefold() in alias_map:
        return alias_map[sender.casefold()]
    if _PHONE.match(sender):
        key = "#" + _phone_key(sender)
        if key in alias_map:
            return alias_map[key]
        masked.setdefault(sender, phone_label(sender))
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


def mask_numbers(text: str, alias_map: dict[str, str]) -> str:
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
        pieces.append(label or phone_label(number))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)
