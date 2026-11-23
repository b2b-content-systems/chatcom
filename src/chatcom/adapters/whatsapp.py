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

import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters.source import ParseResult
from chatcom.core.clock import localize
from chatcom.core.errors import ChatcomError
from chatcom.core.masking import alias_map, canonical, is_phone, mask_numbers, phone_label  # noqa: F401 (re-exported)
from chatcom.core.messages import message_hash, normalize_text, utc_minute

_alias_map = alias_map  # the shipped name, kept for callers

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


@dataclass
class _Raw:
    line: int
    d: tuple[int, int, int]
    t: tuple[int, int, int, str | None]
    rest: str
    extra: list[str] = field(default_factory=list)


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


def parse(text: str, tz_name: str, *, aliases: dict[str, list[str]] | None = None,
          key: bytes | None = None) -> ParseResult:
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

    amap = alias_map(aliases or {})
    masked: dict[str, str] = {}
    group_name = None
    system = 0
    msgs: list[dict] = []
    ordinals: Counter = Counter()
    prev_utc: datetime | None = None
    for line_no, naive, body in rows:
        aware = localize(naive, tz, prev_utc)
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
        text = mask_numbers(text, amap, key)
        name = canonical(sender_n, amap, masked, key)
        label = phone_label(sender_n, key) if is_phone(sender_n) else sender_n
        minute = utc_minute(aware)
        slot = (label, minute)
        ordinal = ordinals[slot]
        ordinals[slot] += 1
        msgs.append({
            "id": message_hash(label, minute, ordinal, text, media, deleted, key),
            "sender": name,
            "time": aware.isoformat(timespec="seconds"),
            "time_precision": "minute",
            "text": text,
            "media": media,
            "deleted": deleted,
            "edited": edited,
            "reply_to": None,
            "reactions": None,
            "quote": None,
            "source": {"adapter": "whatsapp", "line": line_no, "label": label},
        })
    return ParseResult(msgs, system, unparsed, len(rows), fmt, group_name, masked, "whatsapp")


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




# ---- registry hooks ----------------------------------------------------------

def _whatsapp_lines(text: str) -> tuple[int, int]:
    """(lines that read as WhatsApp message headers, non-blank lines) in a sample."""
    hits = total = 0
    for ln in text.replace("\r\n", "\n").split("\n")[:400]:
        if not ln.strip():
            continue
        total += 1
        i, a, _ = _match(ln)
        hits += bool(i or a)
    return hits, total


def _looks_whatsapp(text: str) -> bool:
    hits, total = _whatsapp_lines(text)
    return hits >= 2 and hits >= 0.3 * total or (hits == 1 and total <= 3)


def matches_container(paths: list[Path]) -> bool:
    """A .zip is WhatsApp's unless it holds no text file at all. WhatsApp is
    the only zip source, so a broken zip or one with several text files is
    claimed here and read_export reports the exact CHATCOM_E_EXPORT_ZIP."""
    if len(paths) != 1 or not paths[0].is_file() or paths[0].suffix.lower() != ".zip":
        return False
    try:
        with zipfile.ZipFile(paths[0]) as zf:
            return any(i.filename.lower().endswith(".txt") and not i.is_dir() for i in zf.infolist())
    except (zipfile.BadZipFile, OSError):
        return True


def matches_text(paths: list[Path]) -> bool:
    if len(paths) != 1 or not paths[0].is_file() or paths[0].suffix.lower() in (".zip", ".json"):
        return False
    from chatcom.adapters.registry import peek
    return _looks_whatsapp(peek(paths[0]))


def load(paths: list[Path], ctx) -> tuple[ParseResult, str]:
    text, display = read_export(paths[0])
    return parse(text, ctx.tz, aliases=ctx.aliases, key=ctx.key), display
