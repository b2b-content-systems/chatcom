"""The writer-output validator. The JSON Schema checks shape; this checks the
rules a schema cannot: ids must exist, the words must fit the segment budget
at phone reading speed, and the writer never emits a path or a link (assets
are referenced only by ids). Every problem is {path, message}."""
from __future__ import annotations

import re

from chatcom.core import budget
from chatcom.core.bible import current_code, episode_number
from chatcom.core.schema import problems

_LINKISH = re.compile(r"(\w+://|\bwww\.|\bfile:|(?:^|\s)~?/[\w.\-]+/|\b[A-Za-z]:\\)", re.I)


def _words(text: str) -> int:
    return len(text.split())


def validate(beats, *, bible: dict, cast_sheet: dict, new_messages: int) -> list[dict]:
    errs = problems("beats", beats)
    if errs:
        return errs
    out: list[dict] = []

    def err(path: str, message: str) -> None:
        out.append({"path": path, "message": message})

    code = current_code(bible)
    n = episode_number(code)
    if beats["episode"] != code:
        err("$.episode", f"must be {code}, the episode being written")
    if bible["show"]["title"] is None and not beats["show_title"]:
        err("$.show_title", "the show has no title yet: name it (at most 40 characters)")

    cast = {m["name"] for m in bible["cast"]}
    chorus = set(bible["chorus"])
    poses = set(cast_sheet["poses"])
    known_bgs = {b["id"] for b in cast_sheet["backgrounds"]}
    new_locs: list[str] = []

    scenes = [("$.cold_open", "cold_open", beats["cold_open"])] + [
        (f"$.scenes[{i}]", f"scene_{i + 1}", s) for i, s in enumerate(beats["scenes"])]
    for path, seg, scene in scenes:
        nl = scene.get("new_location")
        if nl:
            if nl["id"] in known_bgs:
                err(f"{path}.new_location.id", f"'{nl['id']}' already exists; use it as the background instead")
            elif nl["id"] != scene["background"]:
                err(f"{path}.background", f"must equal new_location.id '{nl['id']}'")
            else:
                new_locs.append(nl["id"])
                known_bgs.add(nl["id"])
        elif scene["background"] not in known_bgs:
            err(f"{path}.background", f"unknown background '{scene['background']}'; use one of {sorted(known_bgs)} or add a new_location")
        words = 0
        for j, b in enumerate(scene["bubbles"]):
            bp = f"{path}.bubbles[{j}]"
            words += _words(b["text"])
            if len(b["text"]) > budget.BUBBLE_MAX_CHARS:
                err(f"{bp}.text", f"{len(b['text'])} characters; a bubble holds at most {budget.BUBBLE_MAX_CHARS}")
            if b["speaker"] in cast:
                if b["pose"] is None:
                    err(f"{bp}.pose", f"{b['speaker']} is cast: pick a pose from {sorted(poses)}")
                elif b["pose"] not in poses:
                    err(f"{bp}.pose", f"unknown pose '{b['pose']}'")
            elif b["speaker"] in chorus:
                if b["pose"] is not None:
                    err(f"{bp}.pose", f"{b['speaker']} is chorus (not drawn): pose must be null")
            else:
                err(f"{bp}.speaker", f"'{b['speaker']}' is not in the cast or chorus")
        limit = int(budget.seconds(n, seg) * budget.WORDS_PER_SECOND)
        if words > limit:
            err(path, f"{words} words do not read in {budget.seconds(n, seg):.1f} s; at most {limit}")
    if len(new_locs) > budget.MAX_NEW_LOCATIONS:
        err("$.scenes", f"{len(new_locs)} new locations; at most {budget.MAX_NEW_LOCATIONS} per episode, reuse a background")

    credited = [c["name"] for c in beats["credits"]]
    for i, name in enumerate(credited):
        if name not in cast:
            err(f"$.credits[{i}].name", f"'{name}' is not in the cast")
    if len(set(credited)) != len(credited):
        err("$.credits", "a cast member is credited twice")
    missing = sorted(cast - set(credited))
    if missing:
        err("$.credits", f"every cast member gets a credit; missing {missing}")

    if _words(beats["synopsis"]) > budget.SYNOPSIS_MAX_WORDS:
        err("$.synopsis", f"{_words(beats['synopsis'])} words; at most {budget.SYNOPSIS_MAX_WORDS}")

    upd = beats["bible_update"]
    jokes = {j["id"] for j in bible["jokes"]}
    for i, jid in enumerate(upd["callbacks"]):
        if jid not in jokes:
            err(f"$.bible_update.callbacks[{i}]", f"no joke '{jid}' in the bible")
    stories = {s["id"] for s in bible["storylines"]}
    for i, su in enumerate(upd["storylines_update"]):
        if su["id"] not in stories:
            err(f"$.bible_update.storylines_update[{i}].id", f"no storyline '{su['id']}' in the bible")
    for i, c in enumerate(upd["cast"]):
        if c["name"] not in cast:
            err(f"$.bible_update.cast[{i}].name", f"'{c['name']}' is not in the cast")
    known_text = {j["joke"].casefold() for j in bible["jokes"]}
    for i, t in enumerate(upd["jokes_new"]):
        if t.casefold() in known_text:
            err(f"$.bible_update.jokes_new[{i}]", "already in the ledger; list its id under callbacks instead")

    if new_messages == 0 and not beats["bottle"]:
        err("$.bottle", "no new messages this week: this must be a bottle episode")
    if beats["bottle"] and bible["jokes"] and not upd["callbacks"]:
        err("$.bible_update.callbacks", "a bottle episode is built from callbacks: call back at least one joke")

    for path, text in _texts(beats):
        if _LINKISH.search(text):
            err(path, "no links or file paths in on-screen text")
    return sorted(out, key=lambda e: e["path"])


def _texts(beats: dict):
    yield "$.title", beats["title"]
    if beats["show_title"]:
        yield "$.show_title", beats["show_title"]
    yield "$.tv_guide", beats["tv_guide"]
    yield "$.synopsis", beats["synopsis"]
    for i, c in enumerate(beats["credits"]):
        yield f"$.credits[{i}].role", c["role"]
    for path, scene in [("$.cold_open", beats["cold_open"])] + [(f"$.scenes[{i}]", s) for i, s in enumerate(beats["scenes"])]:
        for j, b in enumerate(scene["bubbles"]):
            yield f"{path}.bubbles[{j}].text", b["text"]
        if scene.get("new_location"):
            yield f"{path}.new_location.description", scene["new_location"]["description"]
