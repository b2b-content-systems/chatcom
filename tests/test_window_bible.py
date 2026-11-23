import copy
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from chatcom.core import bible as bible_ops
from chatcom.core import ranking, window
from chatcom.core.errors import ChatcomError
from chatcom.core.messages import message_hash, utc_minute


def msg(i, sender, text, day0=datetime(2026, 1, 1, 12, tzinfo=timezone.utc), step=timedelta(hours=6)):
    t = day0 + step * i
    return {"id": message_hash(sender, utc_minute(t), 0, text, None, False), "sender": sender,
            "time": t.isoformat(timespec="seconds"), "text": text, "media": None, "deleted": False,
            "edited": False, "reply_to": None, "reactions": None, "source": {"adapter": "synthetic", "line": i + 1}}


CHAT = [msg(i, ["Ava", "Ryan", "Nora"][i % 3], f"line {i}") for i in range(120)]  # 30 days


def test_first_episode_is_last_seven_days():
    w = window.select(CHAT, None)
    assert w.end - w.start <= timedelta(days=7)
    assert w.messages[-1] is CHAT[-1] and w.gap is None
    assert {k: w.cursor[k] for k in ("minute", "hash")} == {"minute": utc_minute(datetime.fromisoformat(CHAT[-1]["time"])), "hash": CHAT[-1]["id"]}
    assert w.cursor["adapter"] == "whatsapp" and w.cursor["precision"] == "minute" and len(w.cursor["signature"]["raw"]) == 5


def test_next_episode_starts_after_the_cursor_and_dedupes_overlap():
    w1 = window.select(CHAT[:60], None)
    w2 = window.select(CHAT[:60] + CHAT[40:80], w1.cursor)  # overlapping export
    assert w2.messages[0] is CHAT[60] and w2.messages[-1] is CHAT[79] and len(w2.messages) == 20


def test_cursor_message_missing_falls_back_to_its_minute():
    w1 = window.select(CHAT[:60], None)
    w2 = window.select(CHAT[61:90], {**w1.cursor, "hash": "0" * 16})
    assert w2.messages[0] is CHAT[61]


def test_cap_skips_old_unread_and_logs_the_gap():
    w1 = window.select(CHAT[:10], None)
    w2 = window.select(CHAT, w1.cursor, max_days=14)
    assert w2.end - w2.start <= timedelta(days=14)
    assert w2.gap and w2.gap["skipped"] == len(CHAT) - 10 - len(w2.messages)
    assert w2.log_entry()["gap"] == w2.gap


def test_stale_export_fails_before_any_model_call():
    w1 = window.select(CHAT, None)
    with pytest.raises(ChatcomError) as e:
        window.select(CHAT, w1.cursor)
    assert e.value.code == "CHATCOM_E_STALE_EXPORT" and "--bottle" in e.value.fix
    b = window.select(CHAT, w1.cursor, bottle=True)
    assert b.messages == [] and b.cursor == w1.cursor


def test_exclusion_drops_lines_blanks_mentions_and_still_advances_cursor():
    chat = CHAT[:-1] + [msg(119, "Ava", "@Nora are you coming"), msg(120, "Nora", "last word")]
    w = window.select(chat, None, exclude=["Nora"])
    assert all(m["sender"] != "Nora" for m in w.messages)
    assert w.messages[-1]["text"] == "@[someone] are you coming"
    assert w.cursor["hash"] == chat[-1]["id"] and w.excluded > 0


def test_pick_cast_caps_at_eight_and_rest_is_chorus():
    counts = Counter({f"P{i}": 100 - i for i in range(11)})
    cast, chorus = bible_ops.pick_cast(counts, no_cast=["P0"], exclude=["P1"])
    assert cast == [f"P{i}" for i in range(2, 10)] and chorus == ["P0", "P10"]


def _bible():
    return bible_ops.new_bible(slug="s", title=None, look="sitcom", operator_sender="Dave", timezone="UTC",
                               created="2026-01-01T00:00:00+00:00", cast=["Dave", "Mike", "Theo", "Priya", "Sam"],
                               chorus=[])


WIN = {"from": "2026-01-01T00:00:00+00:00", "to": "2026-01-02T00:00:00+00:00",
       "cursor": {"minute": "2026-01-02T00:00Z", "hash": "a" * 16, "adapter": "whatsapp", "precision": "minute", "signature": {"raw": [], "canonical": []}}, "message_count": 5, "gap": None}
WRITER = {"mode": "replay", "model": "replay", "attempts": 1}


def test_apply_beats_builds_the_memory(beats_s01e01):
    b0 = _bible()
    b1 = bible_ops.apply_beats(b0, beats_s01e01, window_entry=WIN, writer=WRITER)
    assert b0["episodes"] == [] and b0["show"]["title"] is None  # input untouched
    assert b1["show"]["title"] == "LAKE HOUSE, PROBABLY" and b1["show"]["next_episode"] == 2
    assert [j["id"] for j in b1["jokes"]] == ["j1", "j2", "j3"]
    assert [s["id"] for s in b1["storylines"]] == ["s1", "s2"]
    mike = [m for m in b1["cast"] if m["name"] == "Mike"][0]
    assert mike["role"] == "Probably" and "commits to nothing" in mike["traits"]
    assert b1["episodes"][0]["code"] == "S01E01" and b1["episodes"][0]["window"] == WIN

    # Episode 2 calls back to episode 1 and resolves a storyline.
    e2 = copy.deepcopy(beats_s01e01)
    e2.update(episode="S01E02", show_title=None, title="The Floor")
    e2["bible_update"] = {"cast": [], "jokes_new": ["Priya's sister has not been told about the floor"],
                          "callbacks": ["j1"], "storylines_new": [],
                          "storylines_update": [{"id": "s2", "status": "resolved", "summary": "Mike took the floor"}]}
    b2 = bible_ops.apply_beats(b1, e2, window_entry=WIN, writer=WRITER)
    assert b2["jokes"][0]["last_callback"] == "S01E02" and b2["jokes"][-1]["id"] == "j4"
    assert b2["storylines"][1]["status"] == "resolved"
    w = bible_ops.windowed(b2)
    assert w["episode"] == "S01E03" and [s["id"] for s in w["storylines"]] == ["s1"]
    assert [p["code"] for p in w["previously"]] == ["S01E01", "S01E02"]


def test_apply_beats_refuses_wrong_episode(beats_s01e01):
    with pytest.raises(ChatcomError) as e:
        bible_ops.apply_beats(_bible(), {**beats_s01e01, "episode": "S01E04"}, window_entry=WIN, writer=WRITER)
    assert e.value.code == "CHATCOM_E_STATE"


def test_sampling_keeps_order_every_sender_and_the_laughed_at_line():
    chat = [msg(i, ["Ava", "Ryan"][i % 2], "x " * 60, step=timedelta(minutes=1)) for i in range(200)]
    chat.insert(100, msg(1000, "Nora", "the quiet one speaks", step=timedelta(minutes=1)))
    joke = msg(150, "Ryan", "short joke", step=timedelta(minutes=1))
    laughs = [{**msg(150, s, "lol", step=timedelta(minutes=1)), "id": f"{n:016x}"} for n, s in enumerate(["Ava", "Nora"])]
    chat = sorted(chat + [joke] + laughs, key=lambda m: m["time"])
    kept, stats = ranking.sample(chat, token_budget=1500)
    assert stats["sampled"] and stats["tokens"] <= 1500
    assert [m["time"] for m in kept] == sorted(m["time"] for m in kept)
    assert {"Ava", "Ryan", "Nora"} <= {m["sender"] for m in kept}
    assert joke in kept
