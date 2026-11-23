"""The 60-second vertical cut, segment by segment. Both versions sum to 60 s:
episode 1 has no 'Previously on', so its three scenes get the 5 s."""
from __future__ import annotations

SCENES_EP1 = 37.0
SCENES_LATER = 32.0
CREDIT_CARDS = 4
ROLES_PER_CARD = 2
ROLE_MAX_CHARS = 32
MAX_CAST = CREDIT_CARDS * ROLES_PER_CARD
SYNOPSIS_MAX_WORDS = 18
BUBBLE_MAX_CHARS = 90
WORDS_PER_SECOND = 3.0       # sound-off reading speed on a phone
MAX_NEW_LOCATIONS = 2        # image calls per episode


def segments(episode_number: int) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    if episode_number >= 2:
        out.append(("previously_on", 5.0))
    out += [("cold_open", 6.0), ("title_card", 4.0), ("credits", 8.0)]
    scenes = SCENES_EP1 if episode_number == 1 else SCENES_LATER
    out += [(f"scene_{i}", scenes / 3) for i in (1, 2, 3)]
    out += [("tv_guide", 2.0), ("renewal", 3.0)]
    return out


def seconds(episode_number: int, name: str) -> float:
    return dict(segments(episode_number))[name]


def summary(episode_number: int) -> dict:
    return {
        "segments": [{"name": n, "seconds": round(s, 3)} for n, s in segments(episode_number)],
        "total_seconds": round(sum(s for _, s in segments(episode_number)), 3),
        "max_words": {n: int(s * WORDS_PER_SECOND) for n, s in segments(episode_number)
                      if n == "cold_open" or n.startswith("scene_")},
        "bubble_max_chars": BUBBLE_MAX_CHARS,
        "role_max_chars": ROLE_MAX_CHARS,
        "synopsis_max_words": SYNOPSIS_MAX_WORDS,
        "max_new_locations": MAX_NEW_LOCATIONS,
        "max_credits": MAX_CAST,
    }
