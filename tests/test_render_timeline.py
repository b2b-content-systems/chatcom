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
    assert e.value.code == "CHATCOM_E_BEATS_INVALID" and "chatcom ingest" in e.value.fix  # a path that works


# Value: protects=the writer's validator refuses a draft the timeline cannot fit, and passes one it can;
# fails_when=validate stops calling the fit rule or reports it at the wrong path; why_new=the old check read the
# source text, not the result; seam=none; no_claim=the writer's retry loop (validator called directly)
def test_the_beats_validator_runs_the_same_fit_check():
    from chatcom.core import bible as bible_ops
    bib = bible_ops.new_bible(slug="s", title=None, look="sitcom", operator_sender="Dave", timezone="UTC",
                              created="2026-01-01T00:00:00+00:00", cast=["Dave", "Mike", "Theo", "Priya", "Sam"],
                              chorus=["Jonah"])
    cs = bible_ops.new_cast_sheet(bib)
    assert beats_ops.validate(beats(), bible=bib, cast_sheet=cs, new_messages=25) == []
    b = beats()
    b["cold_open"]["bubbles"] = [dict(b["cold_open"]["bubbles"][0], text=w, sticker=None) for w in "a b c d e f".split()]
    errs = beats_ops.validate(b, bible=bib, cast_sheet=cs, new_messages=25)  # write time and render time use one rule
    assert [e["path"] for e in errs] == ["$.cold_open.bubbles"] and "at most 5" in errs[0]["message"]


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
    # Value: protects=scene bubbles are timed from the episode start, inside their own segment; fails_when=the
    # segment offset is dropped and later scenes play over the cold open; why_new=cold open starts at 0 in E01, so the
    # check above cannot see an offset; seam=none; no_claim=none beyond protects
    for code in ("S01E01", "S01E02"):
        bb = beats(code)
        for name in render.SCENE_SEGMENTS:
            seg = render.timeline(bb, sheet(bb), None, show_title="X").segment(name)
            assert seg.bubbles and all(seg.start <= x.start < x.end <= seg.start + seg.length for x in seg.bubbles)
    # Value: protects=a chorus speaker (pose null) shows a bubble with no drawing and needs none; fails_when=the pose
    # check or the cast list stops skipping pose None; why_new=the fixture has no chorus bubble; seam=none;
    # no_claim=how a chorus label looks
    b = beats()
    b["cold_open"]["bubbles"][2] = dict(b["cold_open"]["bubbles"][2], speaker="Jonah", pose=None)
    cold = render.timeline(b, sheet(beats()), None, show_title="X").segment("cold_open")
    assert cold.bubbles[2].speaker == "Jonah" and cold.bubbles[2].pose is None and "Jonah" not in cold.cast


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
    # Value: protects=a full cast of 8 fills exactly 4 cards and a 9th credit is refused, not drawn on a 5th card;
    # fails_when=the card guard is removed or off by one; why_new=the fixture has 5 credits, far from the cap;
    # seam=none; no_claim=none beyond protects
    sh = sheet(b)
    b["credits"] = [{"name": f"P{i}", "role": "r"} for i in range(budget.MAX_CAST)]
    assert [len(c) for c in render.timeline(b, sh, None, show_title="X").segment("credits").cards] == [2, 2, 2, 2]
    b["credits"].append({"name": "P9", "role": "r"})
    with pytest.raises(ChatcomError) as e:  # a draft that skipped validation
        render.timeline(b, sh, None, show_title="X")
    assert e.value.code == "CHATCOM_E_BEATS_INVALID"


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


def test_the_writer_is_told_each_segments_bubble_limit_from_the_same_rule(lake_show):
    from chatcom.core import bundle
    for n in (1, 2):
        limits = render.bubble_limits(n)
        assert limits["cold_open"] == 5 and limits["scene_1"] == 6  # 6 s holds 5; a scene slot holds the schema's 6
        for seg, most in limits.items():
            length = budget.seconds(n, seg)
            assert render.fits(["x"] * most, length) and (most == 6 or not render.fits(["x"] * (most + 1), length))
    bible = json.loads(lake_show.bible_path.read_text("utf-8"))
    sheet_doc = json.loads(lake_show.cast_path.read_text("utf-8"))
    b = bundle.build(bible, sheet_doc, [])  # the prompt reads the number from here, never states one
    assert b["budget"]["max_bubbles"] == render.bubble_limits(1)
