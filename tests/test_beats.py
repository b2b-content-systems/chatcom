import copy

import pytest

from chatcom.core import beats as beats_ops
from chatcom.core import bible as bible_ops


@pytest.fixture
def ctx():
    b = bible_ops.new_bible(slug="s", title=None, look="sitcom", operator_sender="Dave", timezone="UTC",
                            created="2026-01-01T00:00:00+00:00", cast=["Dave", "Mike", "Theo", "Priya", "Sam"],
                            chorus=["Jonah"])
    return b, bible_ops.new_cast_sheet(b)


def problems(beats, ctx, new_messages=25):
    b, sheet = ctx
    return beats_ops.validate(beats, bible=b, cast_sheet=sheet, new_messages=new_messages)


def paths(errs):
    return [e["path"] for e in errs]


def test_recorded_draft_is_valid(beats_s01e01, ctx):
    assert problems(beats_s01e01, ctx) == []


def test_schema_problems_come_first(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["scenes"].append(d["scenes"][0])
    d["surprise"] = 1
    errs = problems(d, ctx)
    assert "$" in paths(errs) or "$.scenes" in paths(errs)


def test_ids_must_exist(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["scenes"][0]["background"] = "mars_base"
    d["scenes"][0]["bubbles"][0]["speaker"] = "Zed"
    d["bible_update"]["callbacks"] = ["j9"]
    d["bible_update"]["storylines_update"] = [{"id": "s4", "status": "resolved"}]
    errs = paths(problems(d, ctx))
    assert "$.scenes[0].background" in errs and "$.scenes[0].bubbles[0].speaker" in errs
    assert "$.bible_update.callbacks[0]" in errs and "$.bible_update.storylines_update[0].id" in errs


def test_poses_cast_vs_chorus(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["scenes"][0]["bubbles"][0]["pose"] = None
    d["scenes"][0]["bubbles"][1] = {"speaker": "Jonah", "text": "hi", "pose": "talking", "sticker": None}
    errs = paths(problems(d, ctx))
    assert "$.scenes[0].bubbles[0].pose" in errs and "$.scenes[0].bubbles[1].pose" in errs


def test_reading_speed_and_bubble_length(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["cold_open"]["bubbles"][0]["text"] = " ".join(["word"] * 17)  # 17 + 4 others > 18
    d["scenes"][1]["bubbles"][0]["text"] = "x" * 91
    errs = problems(d, ctx)
    assert any(e["path"] == "$.cold_open" and "read in 6.0 s" in e["message"] for e in errs)
    assert "$.scenes[1].bubbles[0].text" in paths(errs)


def test_new_location_rules(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    for i, s in enumerate(d["scenes"]):
        s["background"] = f"place_{i}"
        s["new_location"] = {"id": f"place_{i}", "description": "somewhere new"}
    errs = problems(d, ctx)
    assert any("3 new locations" in e["message"] for e in errs)
    d = copy.deepcopy(beats_s01e01)
    d["scenes"][0]["new_location"] = {"id": "home_base", "description": "x"}
    assert "$.scenes[0].new_location.id" in paths(problems(d, ctx))
    d = copy.deepcopy(beats_s01e01)  # a new location may be reused by a later scene
    d["scenes"][2]["background"] = "lake_house"
    assert problems(d, ctx) == []


def test_every_cast_member_credited_once(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["credits"][4] = {"name": "Dave", "role": "Again"}
    msgs = [e["message"] for e in problems(d, ctx)]
    assert any("credited twice" in m for m in msgs) and any("missing ['Sam']" in m for m in msgs)


def test_synopsis_title_and_links(beats_s01e01, ctx):
    d = copy.deepcopy(beats_s01e01)
    d["synopsis"] = " ".join(["word"] * 19)
    d["show_title"] = None
    d["tv_guide"] = "see www.example.com tonight"
    d["scenes"][0]["bubbles"][0]["text"] = "cat ~/secrets/keys.txt"
    errs = paths(problems(d, ctx))
    assert {"$.synopsis", "$.show_title", "$.tv_guide", "$.scenes[0].bubbles[0].text"} <= set(errs)


def test_bottle_rules(beats_s01e01, ctx):
    b, sheet = ctx
    assert "$.bottle" in paths(beats_ops.validate(beats_s01e01, bible=b, cast_sheet=sheet, new_messages=0))
    b1 = bible_ops.apply_beats(b, beats_s01e01, window_entry={
        "from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00",
        "cursor": {"minute": "2026-01-02T00:00Z", "hash": "a" * 16}, "message_count": 5, "gap": None},
        writer={"mode": "replay", "model": "replay", "attempts": 1})
    d = copy.deepcopy(beats_s01e01)
    d.update(episode="S01E02", show_title=None, bottle=True)
    d["bible_update"]["jokes_new"] = []
    errs = beats_ops.validate(d, bible=b1, cast_sheet=sheet, new_messages=0)
    assert "$.bible_update.callbacks" in paths(errs)
    d["bible_update"]["callbacks"] = ["j2"]
    errs = beats_ops.validate(d, bible=b1, cast_sheet=sheet, new_messages=0)
    # episode 2 has a "Previously on", so its scenes are 5 s shorter
    assert [e["path"] for e in errs] == ["$.scenes[1]"] and "10.7 s" in errs[0]["message"]
    del d["scenes"][1]["bubbles"][3]
    assert beats_ops.validate(d, bible=b1, cast_sheet=sheet, new_messages=0) == []


def test_repeated_joke_must_be_a_callback(beats_s01e01, ctx):
    b, sheet = ctx
    b1 = bible_ops.apply_beats(b, beats_s01e01, window_entry={
        "from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00",
        "cursor": {"minute": "2026-01-02T00:00Z", "hash": "a" * 16}, "message_count": 5, "gap": None},
        writer={"mode": "replay", "model": "replay", "attempts": 1})
    d = copy.deepcopy(beats_s01e01)
    d.update(episode="S01E02", show_title=None)
    errs = beats_ops.validate(d, bible=b1, cast_sheet=sheet, new_messages=10)
    assert "$.bible_update.jokes_new[0]" in paths(errs)
