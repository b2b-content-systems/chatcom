"""Render, part 1: the episode timeline (core/render.py). Pure: no files, no
HyperFrames. The beats come from the synthetic lake-house writer fixture."""
import copy
import json
from pathlib import Path

import pytest

from chatcom.core import beats as beats_ops, budget, render
from chatcom.core.errors import ChatcomError

FIXTURE = Path(__file__).parent / "fixtures" / "writer" / "lake-house-S01E01.json"
POSES = ("neutral", "talking", "laughing", "typing", "facepalm", "shrug")


# Value: protects=the 60 s cut, every bubble on screen long enough to read, no dead end after paid casting;
# fails_when=a segment length drifts from budget.py, compression drops or overlaps bubbles, the recap reads the
# episode being made, or a missing drawing renders blank; why_new=new module; seam=none; no_claim=how it looks

def beats(episode="S01E01"):
    b = json.loads(FIXTURE.read_text("utf-8"))
    b["episode"] = episode
    return b


def sheet(b):
    names = {c["name"] for c in b["credits"]}
    bgs = {s["background"] for s in [b["cold_open"], *b["scenes"]]}
    return {"members": [{"name": n, "poses": {p: {"file": f"{n}/{p}.png"} for p in POSES}} for n in names],
            "backgrounds": [{"id": i, "file": f"bg/{i}.png"} for i in bgs]}


def test_both_versions_are_sixty_seconds_and_follow_the_budget():
    for code, n in (("S01E01", 1), ("S01E02", 2)):
        b = beats(code)
        tl = render.timeline(b, sheet(b), None, show_title="LAKE HOUSE")
        assert tl.total == pytest.approx(60.0)
        assert [(s.name, s.length) for s in tl.segments] == budget.segments(n)  # one budget, never a copy
        starts = [s.start for s in tl.segments]
        assert starts == sorted(starts) and starts[0] == 0.0
    assert render.timeline(beats(), sheet(beats()), None, show_title="X").segments[0].name == "cold_open"


def test_a_bubble_stays_up_long_enough_to_read_within_a_floor_and_a_cap():
    assert render.hold("k") == render.HOLD_FLOOR
    assert render.hold("one two three four five six") == pytest.approx(2.0)
    assert render.hold(" ".join(["word"] * 40)) == render.HOLD_CAP


def test_a_crowded_scene_shortens_every_hold_and_keeps_every_bubble():
    texts = [" ".join(["word"] * 12)] * 6  # 6 bubbles of 12 words: 24 s of reading in a 10.7 s slot
    spots = render.place(texts, 32.0 / 3)
    assert len(spots) == 6
    for (a, z), (a2, _) in zip(spots, spots[1:]):
        assert z - a >= render.HOLD_FLOOR - 1e-6 and a2 >= z  # long enough, and no overlap
    assert spots[-1][1] <= 32.0 / 3 + 1e-6


def test_a_scene_that_cannot_fit_is_refused_when_the_script_is_validated():
    b = beats()
    b["cold_open"]["bubbles"] = [dict(b["cold_open"]["bubbles"][0], text=w, sticker=None) for w in "a b c d e f".split()]
    problems = render.fit_problems(b, 1)  # six one-word bubbles pass the word check, not the fit
    assert problems and problems[0]["path"] == "$.cold_open.bubbles" and "at most 5" in problems[0]["message"]
    with pytest.raises(ChatcomError) as e:  # a draft that skipped validation never renders half a scene
        render.timeline(b, sheet(b), None, show_title="X")
    assert e.value.code == "CHATCOM_E_BEATS_INVALID"


def test_the_beats_validator_runs_the_same_fit_check():
    import inspect
    src = inspect.getsource(beats_ops.validate)
    assert "render.fit_problems(beats, n)" in src  # write time and render time use one rule


def test_bubbles_carry_their_times_and_the_drawn_cast_in_order():
    b = beats()
    tl = render.timeline(b, sheet(b), None, show_title="X")
    cold = tl.segment("cold_open")
    assert len(cold.bubbles) == len(b["cold_open"]["bubbles"])
    assert all(cold.start <= x.start < x.end <= cold.start + cold.length for x in cold.bubbles)
    drawn = [x["speaker"] for x in b["cold_open"]["bubbles"] if x["pose"] is not None]
    assert cold.cast == list(dict.fromkeys(drawn))
    for x in tl.segment("scene_2").bubbles:
        if x.sticker:
            assert x.start <= render.sticker_time(x) <= x.end


def test_a_missing_drawing_stops_the_render_and_names_the_fix():
    b = beats()
    sh = sheet(b)
    for m in sh["members"]:
        m["poses"].pop("talking")  # the fixture's first bubble is a 'talking' pose
    with pytest.raises(ChatcomError) as e:
        render.timeline(b, sh, None, show_title="X")
    assert e.value.code == "CHATCOM_E_STATE" and "chatcom cast" in e.value.fix
    sh = sheet(b)
    sh["backgrounds"] = [dict(x, file=None) for x in sh["backgrounds"]]
    with pytest.raises(ChatcomError) as e:
        render.timeline(b, sh, None, show_title="X")
    assert e.value.code == "CHATCOM_E_STATE" and "background" in e.value.what


def test_credits_go_two_to_a_card():
    b = beats()
    cards = render.timeline(b, sheet(b), None, show_title="X").segment("credits").cards
    assert [len(c) for c in cards] == [2, 2, 1] and cards[0][0] == b["credits"][0]


def test_previously_on_recaps_the_last_finished_episode_not_this_one():
    done = {"code": "S01E01", "synopsis": "The trip is off. Again.", "stills": ["episodes/S01E01/stills/1.jpg",
                                                                                  "episodes/S01E01/stills/3.jpg"]}
    committed = {"episodes": [done]}
    making = {"episodes": [done, {"code": "S01E02", "synopsis": "this episode", "stills": []}]}  # bible.next.json
    assert render.previous_entry(committed) is done
    assert render.previous_entry({"episodes": []}) is None
    b = beats("S01E02")
    prev = render.timeline(b, sheet(b), render.previous_entry(committed), show_title="X").segment("previously_on")
    assert prev.text == "The trip is off. Again." and prev.stills == done["stills"]
    assert render.previous_entry(making)["synopsis"] == "this episode"  # why callers must pass the committed bible
    empty = render.timeline(b, sheet(b), {"synopsis": "s", "stills": []}, show_title="X").segment("previously_on")
    assert empty.stills == []


def test_text_is_kept_verbatim_for_the_composer_to_escape():
    b = beats()
    b["cold_open"]["bubbles"][0] = dict(b["cold_open"]["bubbles"][0], text='</div><script>x</script> "onerror=')
    tl = render.timeline(copy.deepcopy(b), sheet(b), None, show_title="X")
    assert tl.segment("cold_open").bubbles[0].text == '</div><script>x</script> "onerror='
