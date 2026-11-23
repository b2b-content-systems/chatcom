"""Casting, as pure rules: which images a show needs, the prompt for each, and
the content key that names it.

    style sheet  1 image: the show's look, passed into every later call
    poses        per cast member, 6 fixed poses; "neutral" first, then the
                 other 5 drawn from it (so a face stays the same)
    backgrounds  the 3 starter sets, then up to 2 new locations per episode

A member is drawn from a reference photo when the operator gave one, else
from how they text (role, traits, texting voice): an original character, never
a guess at a real face. Every image is named by a content key: the same model,
prompt and inputs always give the same key, so a rerun never pays twice."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

POSES = ("neutral", "talking", "laughing", "typing", "facepalm", "shrug")
MAX_NEW_LOCATIONS = 2
GREEN = "a flat, even, pure chroma-key green background (#00B140) with no shadow, floor or props"

LOOK_STYLE = {
    "sitcom": "3D animated feature-film style, warm family-film lighting, soft rounded shapes",
    "anime": "1980s Japanese city-pop anime style, flat cel shading, bright clean colours",
    "cartoon80s": "glossy 1980s Saturday-morning cartoon style, airbrushed shading, bold outlines",
    "truecrime": "grainy true-crime documentary still, muted colours, film grain, realistic but stylised",
    "cutscene": "late-1990s video-game cutscene, low-polygon 3D, PS1-era textures",
    "facetime": "a family video-call frame, soft webcam light, slightly washed-out colours",
    "flipphone": "a 2006 flip-phone camera photo, low resolution, blown highlights, JPEG artefacts",
    "reality": "a reality-TV confessional snapshot, bright flash, candid framing",
}

POSE_TEXT = {
    "neutral": "standing relaxed, facing the viewer, arms at the sides",
    "talking": "mid-sentence, one hand gesturing, mouth open as if speaking",
    "laughing": "laughing hard, eyes squeezed, head tilted back",
    "typing": "looking down at a phone held in both hands, thumbs typing",
    "facepalm": "one hand over the face in disbelief",
    "shrug": "shrugging with both palms up, eyebrows raised",
}


# A starting look for each member cast from how they text, so no two begin alike (two sparse members came out
# as near twins in the first live run, 2026-11-23). Index i picks a different combination for every member.
HAIR = ("short black hair", "long red hair", "a shaved head", "curly blond hair", "a grey ponytail",
        "a brown buzz cut", "wavy dark-brown hair", "dyed blue hair")
BUILD = ("tall and lanky", "short and stocky", "average height, broad shoulders", "small and wiry")
OUTFIT = ("a mustard-yellow", "a navy-blue", "a forest-green", "a burgundy", "a sky-blue", "a charcoal", "an orange",
          "a lavender")


def look_hint(index: int) -> str:
    """Distinct for the first 256 members. The prompt never names a member, so two sparse members with the same
    hint would get the same content key and share one drawing. The first 8 hints never change (cache keys)."""
    h, b, o = len(HAIR), len(BUILD), len(OUTFIT)
    return (f"{HAIR[index % h]}, {BUILD[(index * 3 + index // h) % b]}, wearing "
            f"{OUTFIT[(index * 5 + index // (h * b)) % o]} top")


@dataclass
class Call:
    kind: str                  # style | pose | background
    prompt: str
    member: str | None = None
    pose: str | None = None
    background: str | None = None
    refs: list[str] = field(default_factory=list)  # names of earlier images or "photo:<name>", resolved by the caller
    aspect: str = "3:4"


def style_prompt(look: str, title: str | None) -> str:
    return (f"A style reference frame for an animated TV show{f' called {title}' if title else ''}: "
            f"{LOOK_STYLE[look]}. An empty, inviting living-room set with no people and no text. "
            "This frame sets the palette, lighting and rendering for every later image.")


def describe_member(m: dict) -> str:
    """What the image model is told about someone cast from how they text."""
    bits = []
    if m.get("role"):
        bits.append(f"known in the group as {m['role']}")
    if m.get("traits"):
        bits.append("personality: " + "; ".join(m["traits"][:4]))
    if m.get("texting_voice"):
        bits.append(f"texts like this: {m['texting_voice']}")
    return ", ".join(bits) or "an easy-going member of a friend group"


def pose_prompt(look: str, m: dict, pose: str, *, from_photo: bool, first: bool, index: int = 0) -> str:
    style = LOOK_STYLE[look]
    if first and from_photo:
        who = ("The person in the reference photo, redrawn as a character in this show. Keep their recognisable "
               "features, hair and build; change the clothes to everyday clothes that suit them.")
    elif first:
        who = (f"An original character (not a real or famous person) who is {describe_member(m)}. Start from "
               f"this look: {look_hint(index)}; give them a distinct face that fits that personality.")
    else:
        who = "The same character as in the character reference: same face, hair, build and outfit."
    return (f"{who} Pose: {POSE_TEXT[pose]}. Full body, centred, {style}, matching the style reference. "
            f"On {GREEN}. No text, no logo, no other people.")


def background_prompt(look: str, description: str) -> str:
    return (f"A background set for an animated TV show: {description} {LOOK_STYLE[look]}, matching the style "
            "reference. Wide shot, no people, no text, room in the lower half for characters to stand.")


def plan(sheet: dict, members: list[dict], look: str, title: str | None,
         new_locations: list[dict] | None = None) -> list[Call]:
    """Every image this show still needs, in the order they must be made.
    `members` are bible cast entries (with role and traits); `sheet` is cast.json."""
    calls: list[Call] = []
    if not sheet.get("style_sheet"):
        calls.append(Call("style", style_prompt(look, title), aspect="16:9"))
    by_name = {m["name"]: m for m in sheet["members"]}
    for index, m in enumerate(members):
        entry = by_name.get(m["name"], {"poses": {}, "source": m["source"], "photo": None})
        from_photo = entry.get("source") == "photo" and bool(entry.get("photo"))
        have = entry.get("poses") or {}
        for pose in POSES:
            if pose in have:
                continue
            first = pose == "neutral"
            refs = ["style"] + ([f"photo:{m['name']}"] if first and from_photo else [] if first
                                else [f"pose:{m['name']}:neutral"])
            calls.append(Call("pose", pose_prompt(look, m, pose, from_photo=from_photo, first=first, index=index),
                              member=m["name"], pose=pose, refs=refs))
    for b in sheet["backgrounds"]:
        if not b.get("file"):
            calls.append(Call("background", background_prompt(look, b["description"]), background=b["id"],
                              refs=["style"], aspect="9:16"))
    for loc in (new_locations or [])[:MAX_NEW_LOCATIONS]:
        if not any(b["id"] == loc["id"] for b in sheet["backgrounds"]):
            calls.append(Call("background", background_prompt(look, loc["description"]), background=loc["id"],
                              refs=["style"], aspect="9:16"))
    return calls


def content_key(model: str, call: Call, ref_hashes: list[str]) -> str:
    """sha256 of everything that decides the picture."""
    h = hashlib.sha256()
    for part in (model, call.kind, call.prompt, call.aspect, call.pose or "", *ref_hashes):
        h.update(part.encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()
