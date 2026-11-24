"""The episode timeline: what is on screen, and when, for the 60-second cut.

Pure: no files, no HTML. The segment lengths come from `core/budget.py` (the
same budget the writer is validated against); this module adds only how long
each bubble stays up.

    beats.json + cast.next.json + the previous episode's log entry
        -> timeline() -> Timeline: segments in order, each with its start and
           length; scenes carry their background, cast and timed bubbles

A bubble stays up for as long as it takes to read (WORDS_PER_SECOND), between
HOLD_FLOOR and HOLD_CAP. A scene whose bubbles do not fit its slot has every
hold shortened by the same factor, never below HOLD_FLOOR; no bubble is ever
dropped. `fit_problems()` runs inside the beats validator, so a script that
cannot fit is refused at write time (the writer retries) and timeline() never
meets one."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from chatcom.core import budget
from chatcom.core.errors import ChatcomError

HOLD_FLOOR = 1.0     # seconds: the shortest time a bubble stays up
HOLD_CAP = 4.0       # seconds: the longest, however long the text
LEAD_IN = 0.3        # seconds before a scene's first bubble
GAP = 0.15           # seconds between one bubble going and the next arriving
STICKER_AT = 0.4     # seconds into its bubble that a sticker lands

SCENE_SEGMENTS = ("cold_open", "scene_1", "scene_2", "scene_3")


@dataclass
class Bubble:
    speaker: str
    text: str
    pose: str | None          # None for a chorus speaker (no drawing)
    sticker: str | None
    start: float              # seconds from the start of the episode
    end: float


@dataclass
class Segment:
    name: str                 # a budget segment name: previously_on, cold_open, title_card, ...
    start: float
    length: float
    background: str | None = None
    cast: list[str] = field(default_factory=list)   # drawn speakers, in order of first line
    bubbles: list[Bubble] = field(default_factory=list)
    cards: list[list[dict]] = field(default_factory=list)  # credits: up to 4 cards of 2 {name, role}
    text: str | None = None   # title, TV Guide line, or the previous synopsis
    stills: list[str] = field(default_factory=list)  # previously_on only


@dataclass
class Timeline:
    episode: str
    show_title: str
    title: str
    total: float
    segments: list[Segment]

    def segment(self, name: str) -> Segment:
        return next(s for s in self.segments if s.name == name)


def hold(text: str) -> float:
    """How long a bubble needs on a phone with the sound off."""
    return min(HOLD_CAP, max(HOLD_FLOOR, len(text.split()) / budget.WORDS_PER_SECOND))


def _room(length: float, count: int) -> float:
    return length - LEAD_IN - GAP * max(0, count - 1)


def fits(texts: list[str], length: float) -> bool:
    """True when every bubble can stay up at least HOLD_FLOOR inside the slot."""
    return len(texts) * HOLD_FLOOR <= _room(length, len(texts)) + 1e-9


def place(texts: list[str], length: float) -> list[tuple[float, float]]:
    """(start, end) of each bubble, relative to its segment. Holds shrink by one
    shared factor when they do not fit, but never below HOLD_FLOOR."""
    if not texts:
        return []
    if not fits(texts, length):
        raise ValueError(f"{len(texts)} bubbles cannot fit in {length:.2f} s")
    holds = [hold(t) for t in texts]
    room = _room(length, len(texts))
    if sum(holds) > room:
        lo, hi = 0.0, 1.0   # the largest shared factor k with sum(max(floor, h*k)) <= room
        for _ in range(60):
            k = (lo + hi) / 2
            if sum(max(HOLD_FLOOR, h * k) for h in holds) <= room:
                lo = k
            else:
                hi = k
        holds = [max(HOLD_FLOOR, h * lo) for h in holds]
    out, t = [], LEAD_IN
    for h in holds:
        out.append((round(t, 4), _down(t + h)))  # an end never rounds past the slot
        t += h + GAP
    return out


def _down(x: float) -> float:
    return math.floor(x * 10000 + 1e-9) / 10000


def max_bubbles(length: float) -> int:
    """The most bubbles a slot of `length` seconds can hold at HOLD_FLOOR each."""
    return int((length - LEAD_IN + GAP + 1e-9) // (HOLD_FLOOR + GAP))


def bubble_limits(episode_number: int) -> dict[str, int]:
    """Per scene segment, the most bubbles it can hold (the schema's cap of 6 also applies).
    The writer's bundle carries this, so the prompt never states a number of its own."""
    return {n: min(6, max_bubbles(s)) for n, s in budget.segments(episode_number) if n in SCENE_SEGMENTS}


def fit_problems(beats: dict, episode_number: int) -> list[dict]:
    """Scenes whose bubbles cannot all fit their slot, as validator problems."""
    out = []
    scenes = [("$.cold_open", "cold_open", beats["cold_open"])] + [
        (f"$.scenes[{i}]", f"scene_{i + 1}", s) for i, s in enumerate(beats["scenes"])]
    for path, seg, scene in scenes:
        length = budget.seconds(episode_number, seg)
        texts = [b["text"] for b in scene["bubbles"]]
        if not fits(texts, length):
            most = max_bubbles(length)
            out.append({"path": f"{path}.bubbles", "message":
                        f"{len(texts)} bubbles do not fit in {length:.1f} s; at most {most} "
                        f"(each stays up at least {HOLD_FLOOR:.0f} s)"})
    return out


def previous_entry(bible: dict) -> dict | None:
    """The last finished episode's log entry: the committed bible's last episode.
    (bible.next.json already holds the episode being made; never read it here.)"""
    episodes = bible.get("episodes") or []
    return episodes[-1] if episodes else None


def _cards(credits: list[dict]) -> list[list[dict]]:
    per = budget.ROLES_PER_CARD
    cards = [credits[i:i + per] for i in range(0, len(credits), per)]
    if len(cards) > budget.CREDIT_CARDS:
        raise ChatcomError("CHATCOM_E_BEATS_INVALID", f"{len(credits)} credits need {len(cards)} cards; "
                           f"at most {budget.CREDIT_CARDS}", "Rewrite the episode: credits fit the cast cap.")
    return cards


def _scene(seg: Segment, scene: dict, sheet: dict, offset: float) -> None:
    backgrounds = {b["id"]: b for b in sheet["backgrounds"]}
    if not (backgrounds.get(scene["background"]) or {}).get("file"):
        raise ChatcomError("CHATCOM_E_STATE", f"{seg.name}: background '{scene['background']}' has no picture yet",
                           "Run `chatcom cast <show>` to draw the episode's sets, then render again.",
                           context={"segment": seg.name, "background": scene["background"]})
    members = {m["name"]: m for m in sheet["members"]}
    seg.background = scene["background"]
    try:
        spots = place([b["text"] for b in scene["bubbles"]], seg.length)
    except ValueError:
        raise ChatcomError("CHATCOM_E_BEATS_INVALID", f"{seg.name}: its bubbles cannot fit in {seg.length:.1f} s",
                           "Write this episode again with `chatcom rewrite <show>`; the messages, the transcript "
                           "and the drawings are kept.", recovery="ask", context={"segment": seg.name}) from None
    for b, (a, z) in zip(scene["bubbles"], spots):
        if b["pose"] is not None:
            drawn = (members.get(b["speaker"]) or {}).get("poses") or {}
            if b["pose"] not in drawn:
                raise ChatcomError("CHATCOM_E_STATE", f"{seg.name}: {b['speaker']} has no '{b['pose']}' pose yet",
                                   "Run `chatcom cast <show>` to finish the cast, then render again.",
                                   context={"segment": seg.name, "member": b["speaker"], "pose": b["pose"]})
            if b["speaker"] not in seg.cast:
                seg.cast.append(b["speaker"])
        seg.bubbles.append(Bubble(b["speaker"], b["text"], b["pose"], b["sticker"],
                                  round(offset + a, 4), _down(offset + z)))


def timeline(beats: dict, sheet: dict, previous: dict | None, *, show_title: str) -> Timeline:
    """The whole episode. `sheet` is cast.next.json; `previous` is
    previous_entry() of the committed bible (None for the first episode)."""
    code = beats["episode"]
    number = int(code.split("E", 1)[1])
    scenes = {"cold_open": beats["cold_open"], **{f"scene_{i + 1}": s for i, s in enumerate(beats["scenes"])}}
    segs, t = [], 0.0
    for name, length in budget.segments(number):
        seg = Segment(name, round(t, 4), length)
        if name in scenes:
            _scene(seg, scenes[name], sheet, t)
        elif name == "previously_on":
            seg.text = (previous or {}).get("synopsis")
            seg.stills = list((previous or {}).get("stills") or [])
        elif name == "title_card":
            seg.text = beats["title"]
        elif name == "credits":
            seg.cards = _cards(beats["credits"])
        elif name == "tv_guide":
            seg.text = beats["tv_guide"]
        segs.append(seg)
        t += length
    return Timeline(episode=code, show_title=show_title, title=beats["title"], total=round(t, 4), segments=segs)


def sticker_time(bubble: Bubble) -> float:
    """When a bubble's sticker (and its sound) lands."""
    return round(min(bubble.start + STICKER_AT, (bubble.start + bubble.end) / 2), 4)
