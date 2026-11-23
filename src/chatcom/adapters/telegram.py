"""The Telegram adapter: Telegram Desktop's machine-readable export
(`result.json`, docs/telegram-export.md).

Two shapes:

    one chat      {"name", "type", "id", "messages": [...]}         (ChatExport_*/result.json)
    whole account {"about", "personal_information", ..., "chats": {"list": [{...one chat...}]}}

A whole-account export holds every chat, so `--thread NAME` picks one
(CHATCOM_E_THREAD_REQUIRED lists them). Only that chat's messages are read;
the account's personal information, contacts and other chats never are.

Sniffing peeks at the start of the file and never loads a large one whole.
Media files in the export folder are never opened: a photo, sticker or voice
note becomes a `media` kind."""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chatcom.adapters.source import ParseResult
from chatcom.core.clock import localize
from chatcom.core.errors import ChatcomError
from chatcom.core.masking import alias_map, canonical, is_phone, mask_numbers, phone_label
from chatcom.core.messages import message_hash, normalize_text, utc_minute

MAX_BYTES = 512 * 1024 * 1024
RESULT = "result.json"
DELETED_ACCOUNT = "Deleted Account"
QUOTE_CHARS = 200
MAX_THREADS_LISTED = 20

_MEDIA_TYPE = {"sticker": "sticker", "video_file": "video", "video_message": "video", "voice_message": "audio",
               "audio_file": "audio", "animation": "gif"}
_ONE_CHAT = re.compile(r'"messages"\s*:\s*\[')
_MESSAGE_KEYS = re.compile(r'"(from|actor)"\s*:')
_DATE = re.compile(r'"date"\s*:\s*"\d{4}-\d\d-\d\dT')
_ACCOUNT = re.compile(r'^\s*\{\s*"about"\s*:\s*"[^"]*"\s*,\s*"personal_information"\s*:', re.S)


# ---- sniffing ------------------------------------------------------------------

def _result_path(paths: list[Path]) -> Path | None:
    if len(paths) != 1:
        return None
    p = paths[0]
    if p.is_dir():
        r = p / RESULT
        return r if r.is_file() else None
    return p if p.is_file() and p.suffix.lower() == ".json" else None


def matches_container(paths: list[Path]) -> bool:
    r = _result_path(paths)
    if r is None:
        return False
    from chatcom.adapters.registry import peek
    head = peek(r)
    if not head.lstrip().startswith("{"):
        return False
    if _ACCOUNT.match(head):
        return True
    return bool(_ONE_CHAT.search(head) and _MESSAGE_KEYS.search(head) and _DATE.search(head))


# ---- reading ----------------------------------------------------------------------

def _read(paths: list[Path]) -> tuple[dict, Path]:
    r = _result_path(paths)
    if r is None:
        raise ChatcomError("CHATCOM_E_EXPORT_NOT_FOUND", f"no {RESULT} in {paths[0]}",
                           "Pass the ChatExport folder Telegram Desktop made, or its result.json "
                           "(docs/telegram-export.md).", context={"path": str(paths[0])})
    if paths[0].is_dir() and paths[0].resolve() not in r.resolve().parents:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{RESULT} in {paths[0].name} points outside that folder",
                           "Pass the export folder Telegram made, unchanged.", context={"path": str(r)})
    with open(r, "rb") as fh:
        data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{r.name} is over the {MAX_BYTES} byte limit",
                           "Export a shorter date range (docs/telegram-export.md).", context={"path": str(r)})
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{r.name} is not valid JSON ({type(e).__name__})",
                           "Export again with Format: Machine-readable JSON, and pass it unchanged.",
                           context={"path": str(r)}) from None
    if not isinstance(doc, dict):
        raise ChatcomError("CHATCOM_E_EXPORT_UNREADABLE", f"{r.name} is not a Telegram export",
                           "Export again with Format: Machine-readable JSON.", context={"path": str(r)})
    return doc, r


def _chats(doc: dict) -> list[dict]:
    if isinstance(doc.get("messages"), list):
        return [doc]
    out = []
    for section in ("chats", "left_chats"):
        lst = (doc.get(section) or {}).get("list") if isinstance(doc.get(section), dict) else None
        out += [c for c in (lst or []) if isinstance(c, dict) and isinstance(c.get("messages"), list)]
    return out


def _chat_label(c: dict) -> str:
    return str(c.get("name") or f"chat {c.get('id', '?')}")


def pick_chat(doc: dict, thread: str | None) -> dict:
    chats = _chats(doc)
    if not chats:
        raise ChatcomError("CHATCOM_E_EMPTY_EXPORT", "this Telegram export holds no chat with messages",
                           "Export the chat again with its messages (docs/telegram-export.md).")
    if thread is None and len(chats) == 1:
        return chats[0]
    names = [_chat_label(c) for c in chats]
    if thread is not None:
        want = thread.casefold()
        hits = [c for c in chats if _chat_label(c).casefold() == want or str(c.get("id")) == thread]
        if len(hits) == 1:
            return hits[0]
        what = (f"{len(hits)} chats are named {thread!r}" if hits else f"no chat named {thread!r} in this export")
    else:
        what = f"this export holds {len(chats)} chats"
    shown = names[:MAX_THREADS_LISTED]
    more = f" and {len(names) - len(shown)} more" if len(names) > len(shown) else ""
    raise ChatcomError("CHATCOM_E_THREAD_REQUIRED", what,
                       f"Pass --thread with one of: {', '.join(shown)}{more}. Two chats with one name: pass its id "
                       "instead.",
                       context={"chats": [{"name": _chat_label(c), "id": c.get("id")} for c in chats]})


# ---- parsing ----------------------------------------------------------------------

def _text(t) -> str:
    """Telegram text: a string, or a list of strings and {type, text} entities."""
    if isinstance(t, str):
        return t
    if isinstance(t, list):
        return "".join(p if isinstance(p, str) else str(p.get("text", "")) if isinstance(p, dict) else ""
                       for p in t)
    return ""


def _media(m: dict) -> tuple[str | None, str]:
    """(media kind, extra text such as a poll question)."""
    if m.get("poll"):
        return "poll", str((m["poll"] or {}).get("question", ""))
    if m.get("photo"):
        return "image", ""
    if m.get("media_type") in _MEDIA_TYPE:
        return _MEDIA_TYPE[m["media_type"]], ""
    if m.get("contact_information"):
        return "contact", ""
    if m.get("location_information") or m.get("live_location_period_seconds"):
        return "location", ""
    if m.get("file") or m.get("mime_type"):
        return "document", ""
    if m.get("game_title") or m.get("invoice_information"):
        return "other", ""
    return None, ""


def _when(m: dict, tz: ZoneInfo, prev_utc: datetime | None) -> datetime:
    unix = m.get("date_unixtime")
    if unix is not None and str(unix).isdigit():
        return datetime.fromtimestamp(int(unix), timezone.utc).astimezone(tz)
    naive = datetime.fromisoformat(str(m["date"]))
    if naive.tzinfo is not None:
        return naive.astimezone(tz)
    return localize(naive, tz, prev_utc)


def parse(doc: dict, tz_name: str, *, aliases: dict[str, list[str]] | None = None, key: bytes | None = None,
          thread: str | None = None) -> ParseResult:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise ChatcomError("CHATCOM_E_TIMEZONE", f"unknown timezone {tz_name!r}",
                           "Pass an IANA zone with --tz, for example --tz America/New_York.") from None
    chat = pick_chat(doc, thread)
    amap = alias_map(aliases or {})
    masked: dict[str, str] = {}

    def who(n: str) -> str:
        return n if is_phone(n) else mask_numbers(n, amap, key)

    def person(n: str) -> str:
        return canonical(who(n), amap, masked, key)

    msgs: list[dict] = []
    by_tg: dict = {}
    unparsed: list[tuple[int, str]] = []
    system = 0
    ordinals: Counter = Counter()
    prev_utc: datetime | None = None
    for no, m in enumerate(chat["messages"], start=1):
        if not isinstance(m, dict):
            unparsed.append((no, "not a message object"))
            continue
        if m.get("type") != "message":
            system += 1
            continue
        try:
            aware = _when(m, tz, prev_utc)
        except (KeyError, ValueError, OverflowError, OSError):
            unparsed.append((no, f"message {m.get('id', '?')} has no readable date"))
            continue
        prev_utc = aware.astimezone(timezone.utc)
        raw_name = normalize_text(str(m.get("from") or DELETED_ACCOUNT)) or DELETED_ACCOUNT
        label = phone_label(raw_name, key) if is_phone(raw_name) else who(raw_name)
        media, extra = _media(m)
        body = _text(m.get("text"))
        if extra:
            body = extra + ("\n" + body if body else "")
        text = mask_numbers(normalize_text(body), amap, key)
        minute = utc_minute(aware)
        mid = message_hash(label, minute, ordinals[(label, minute)], text, media, False, key)
        ordinals[(label, minute)] += 1
        reactions = []
        for r in m.get("reactions") or []:
            if not isinstance(r, dict):
                continue
            emoji = r.get("emoji") or ("⭐" if r.get("type") == "paid" else "custom")
            recent = [x for x in (r.get("recent") or []) if isinstance(x, dict)]
            named = [person(normalize_text(str(x.get("from") or DELETED_ACCOUNT))) for x in recent]
            reactions += [{"emoji": emoji, "sender": n, "count": 1} for n in named]
            rest = int(r.get("count") or 0) - len(named)
            if rest > 0:
                reactions.append({"emoji": emoji, "sender": None, "count": rest})
        reply_to, quote = None, None
        target = by_tg.get(m.get("reply_to_message_id"))
        if target is not None:
            reply_to = target["id"]
            quote = {"sender": target["sender"], "text": target["text"][:QUOTE_CHARS]}
        row = {
            "id": mid,
            "sender": person(raw_name),
            "time": aware.isoformat(timespec="seconds"),
            "time_precision": "exact",
            "text": text,
            "media": media,
            "deleted": False,
            "edited": bool(m.get("edited") or m.get("edited_unixtime")),
            "reply_to": reply_to,
            "reactions": reactions or None,
            "quote": quote,
            "source": {"adapter": "telegram", "label": label},
        }
        if m.get("id") is not None:
            by_tg[m["id"]] = row
        msgs.append(row)
    fmt = {"export": "account" if chat is not doc else "chat",
           "clock": "utc" if any(isinstance(m, dict) and m.get("date_unixtime") for m in chat["messages"])
           else "local"}
    group = mask_numbers(normalize_text(_chat_label(chat)), amap, key)
    return ParseResult(msgs, system, unparsed, len(msgs), fmt, group, masked, "telegram")


def load(paths: list[Path], ctx) -> tuple[ParseResult, str]:
    doc, _ = _read(paths)
    res = parse(doc, ctx.tz, aliases=ctx.aliases, key=ctx.key, thread=ctx.thread)
    return res, res.group_name or "Telegram chat"
