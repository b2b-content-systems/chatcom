"""Bible operations. Pure: every function takes and returns plain dicts and
never touches the disk; the show store writes the result atomically, and only
when an episode is committed."""
from __future__ import annotations

import copy
import json
from collections import Counter

from chatcom import SCHEMA_VERSION
from chatcom.core import budget
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import check

LOOKS = ("sitcom", "anime", "cartoon80s", "truecrime", "cutscene", "facetime", "flipphone", "reality")
POSES = ("neutral", "talking", "laughing", "typing", "facepalm", "shrug")
STARTER_BACKGROUNDS = (
    ("home_base", "The group's usual hangout, where the cast lives on screen."),
    ("chat_set", "The group chat as a TV set: a giant phone screen glowing behind the cast."),
    ("outdoor", "An outdoor spot the group would plausibly meet at."),
)
MAX_TRAITS = 8
SYNOPSES_IN_WINDOW = 5
JOKES_IN_WINDOW = 12


def episode_code(season: int, number: int) -> str:
    return f"S{season:02d}E{number:02d}"


def episode_number(code: str) -> int:
    return int(code.split("E", 1)[1])


def pick_cast(counts: Counter, *, cast: list[str] | None = None, no_cast: list[str] | None = None,
              exclude: list[str] | None = None, limit: int = budget.MAX_CAST) -> tuple[list[str], list[str]]:
    """(cast, chorus). Default cast: the most active senders, up to the cap."""
    drop = {n.casefold() for n in (exclude or [])}
    keep_out = {n.casefold() for n in (no_cast or [])}
    senders = [s for s, _ in counts.most_common() if s.casefold() not in drop]
    if cast:
        chosen = [s for s in cast if s.casefold() not in drop][:limit]
    else:
        chosen = [s for s in senders if s.casefold() not in keep_out][:limit]
    chosen_cf = {c.casefold() for c in chosen}
    chorus = [s for s in senders if s.casefold() not in chosen_cf]
    return chosen, chorus


def new_bible(*, slug: str, title: str | None, look: str, operator_sender: str, timezone: str,
              created: str, cast: list[str], chorus: list[str], photos: dict[str, str] | None = None,
              no_photo: list[str] | None = None, aliases: dict[str, list[str]] | None = None) -> dict:
    photos = photos or {}
    no_photo_cf = {n.casefold() for n in (no_photo or [])}
    members = []
    for name in cast:
        if name.casefold() in no_photo_cf:
            source, reason = "behavior", "operator_choice"
        elif name in photos:
            source, reason = "photo", None
        else:
            source, reason = "behavior", "no_photo_supplied"
        members.append({"name": name, "role": None, "traits": [], "texting_voice": None,
                        "source": source, "reason": reason, "aliases": list((aliases or {}).get(name, []))})
    bible = {
        "schema_version": SCHEMA_VERSION,
        "show": {"slug": slug, "title": title, "look": look, "season": 1, "next_episode": 1,
                 "operator_sender": operator_sender, "timezone": timezone, "created": created},
        "cast": members,
        "chorus": chorus,
        "jokes": [],
        "storylines": [],
        "episodes": [],
    }
    validate(bible)
    return bible


def new_cast_sheet(bible: dict, photos: dict[str, str] | None = None) -> dict:
    photos = photos or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "image_model": None,
        "style_sheet": None,
        "poses": list(POSES),
        "members": [{"name": m["name"], "source": m["source"], "reason": m["reason"],
                     "photo": photos.get(m["name"]) if m["source"] == "photo" else None, "poses": {}}
                    for m in bible["cast"]],
        "backgrounds": [{"id": i, "description": d, "file": None, "key": None} for i, d in STARTER_BACKGROUNDS],
    }


def validate(bible: dict) -> None:
    v = bible.get("schema_version") if isinstance(bible, dict) else None
    if isinstance(v, int) and v > SCHEMA_VERSION:
        raise ChatcomError("CHATCOM_E_SCHEMA_NEWER",
                           f"bible.json is schema_version {v}; this chatcom reads up to {SCHEMA_VERSION}",
                           "Upgrade chatcom (pipx upgrade chatcom); never edit schema_version by hand.",
                           context={"schema_version": v})
    check("bible", bible, code="CHATCOM_E_BIBLE_INVALID", what="bible.json is not valid",
          fix="Undo the last hand edit, or restore the snapshot with `chatcom rollback <show>`.")


def load(text: str) -> dict:
    try:
        bible = json.loads(text)
    except json.JSONDecodeError as e:
        raise ChatcomError("CHATCOM_E_BIBLE_INVALID", f"bible.json is not JSON (line {e.lineno}: {e.msg})",
                           "Undo the last hand edit, or restore the snapshot with `chatcom rollback <show>`.") from None
    validate(bible)
    return bible


def current_code(bible: dict) -> str:
    return episode_code(bible["show"]["season"], bible["show"]["next_episode"])


def last_cursor(bible: dict) -> dict | None:
    return bible["episodes"][-1]["window"]["cursor"] if bible["episodes"] else None


def windowed(bible: dict) -> dict:
    """What the writer sees: the cast, active jokes, open storylines and the
    last few synopses. Keeps the writer input small as a show ages."""
    jokes = sorted(bible["jokes"], key=lambda j: j["last_callback"] or j["first_episode"], reverse=True)
    return {
        "show": {k: bible["show"][k] for k in ("title", "look", "season", "next_episode", "operator_sender")},
        "episode": current_code(bible),
        "cast": bible["cast"],
        "chorus": bible["chorus"],
        "jokes": jokes[:JOKES_IN_WINDOW],
        "storylines": [s for s in bible["storylines"] if s["status"] == "open"],
        "previously": [{"code": e["code"], "title": e["title"], "synopsis": e["synopsis"]}
                       for e in bible["episodes"][-SYNOPSES_IN_WINDOW:]],
    }


def apply_beats(bible: dict, beats: dict, *, window_entry: dict, writer: dict) -> dict:
    """The bible after this episode commits. Does not mutate its input."""
    out = copy.deepcopy(bible)
    code = current_code(out)
    if beats["episode"] != code:
        raise ChatcomError("CHATCOM_E_STATE", f"beats are for {beats['episode']} but the show is on {code}",
                           "Rerun `chatcom write-prep` and write the beats for the episode it names.")
    if out["show"]["title"] is None and beats.get("show_title"):
        out["show"]["title"] = beats["show_title"]
    upd = beats["bible_update"]
    by_name = {m["name"]: m for m in out["cast"]}
    for c in upd["cast"]:
        m = by_name.get(c["name"])
        if m is None:
            continue
        if c.get("role"):
            m["role"] = c["role"]
        if c.get("texting_voice"):
            m["texting_voice"] = c["texting_voice"]
        for t in c.get("traits_add", []):
            if t not in m["traits"]:
                m["traits"].append(t)
        m["traits"] = m["traits"][-MAX_TRAITS:]
    for cr in beats["credits"]:
        m = by_name.get(cr["name"])
        if m is not None and not m["role"]:
            m["role"] = cr["role"]
    next_j = 1 + max([int(j["id"][1:]) for j in out["jokes"]] or [0])
    for text in upd["jokes_new"]:
        out["jokes"].append({"id": f"j{next_j}", "joke": text, "first_episode": code, "last_callback": None})
        next_j += 1
    for jid in upd["callbacks"]:
        for j in out["jokes"]:
            if j["id"] == jid:
                j["last_callback"] = code
    next_s = 1 + max([int(s["id"][1:]) for s in out["storylines"]] or [0])
    for text in upd["storylines_new"]:
        out["storylines"].append({"id": f"s{next_s}", "summary": text, "status": "open", "opened": code, "last_seen": code})
        next_s += 1
    for su in upd["storylines_update"]:
        for s in out["storylines"]:
            if s["id"] == su["id"]:
                s["status"] = su["status"]
                s["last_seen"] = code
                if su.get("summary"):
                    s["summary"] = su["summary"]
    out["episodes"].append({
        "code": code, "title": beats["title"], "window": window_entry, "synopsis": beats["synopsis"],
        "bottle": beats["bottle"], "stills": [], "writer": writer, "posted_at": None, "reaction_summary": None,
    })
    out["show"]["next_episode"] += 1
    validate(out)
    return out
