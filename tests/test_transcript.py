"""Step 2.5 PR 2: the transcript adapter (docs/transcript-format.md) and its writer."""
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from chatcom import cli
from chatcom.adapters import registry, transcript as T
from chatcom.core import window
from chatcom.core.errors import ChatcomError
from chatcom.core.messages import UNKEYED
from chatcom.core.schema import problems
from conftest import ROOT, TZ

FIX = ROOT / "tests" / "fixtures" / "transcript"
PASTE = FIX / "lake-paste.txt"


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


def parse(text, **kw):
    return T.parse(text, TZ, key=UNKEYED, **kw)


def without_lines(msgs):
    return [{**m, "source": {k: v for k, v in m["source"].items() if k != "line"}} for m in msgs]


# ---- the format ------------------------------------------------------------------
# Value: protects=every rule in docs/transcript-format.md; fails_when=a rule is parsed differently; why_new=new
# adapter; seam=none; no_claim=formats outside the doc

def test_fixture_parses_every_kind_of_line():
    res = T.parse(PASTE.read_text("utf-8"), TZ, key=UNKEYED)
    assert res.unparsed == [] and res.fmt == {"origin": "paste", "speakers": "listed"}
    m = res.messages
    assert [x["sender"] for x in m] == ["Dave", "Mike", "Theo", "Sam", "Mike", "Dave", "Theo"]
    assert all(x["time_precision"] == "minute" and x["source"]["adapter"] == "transcript" for x in m)
    assert m[0]["time"] == "2026-01-09T19:02:00-05:00"
    assert m[1]["reactions"] == [{"emoji": "😂", "sender": "Theo", "count": 1},
                                 {"emoji": "👍", "sender": None, "count": 2}]
    assert m[1]["text"] == "not me, I brought it last year"
    assert (m[2]["media"], m[2]["text"]) == ("image", "found the receipt\nit was 2023")
    assert m[3]["quote"] == {"sender": "Mike", "text": "not me, I brought it last year"}
    assert m[4]["deleted"] and m[4]["text"] == ""
    assert "555" not in m[5]["text"] and "Unnamed" in m[5]["text"]
    assert (m[6]["media"], m[6]["text"]) == (None, "<image> is how my phone shows pictures")
    assert all(problems("message", x) == [] for x in m)


def test_dates_set_the_precision():
    res = parse("# speakers: A, B\n"
                "A: no date yet\n"
                "--- 2026-03-01 ---\n"
                "B: a day row\n"
                "[09:15] A: a timed row under the date\n"
                "[09:15:30] B: with seconds\n"
                "A: another day row\n")
    p = [(m["time_precision"], m["time"]) for m in res.messages]
    assert p[0] == ("order", "2026-02-28T12:00:00.000-05:00")  # the day before the first date shown
    assert p[1] == ("day", "2026-03-01T09:14:59.999-05:00")  # just before the next timed row that day
    assert p[2] == ("minute", "2026-03-01T09:15:00-05:00") and p[3] == ("exact", "2026-03-01T09:15:30-05:00")
    assert p[4][0] == "day"
    times = [datetime.fromisoformat(t) for _, t in p]
    assert times[4] > times[3]  # synthetic rows come after the rows before them


def test_synthetic_times_are_strictly_increasing():
    res = parse("# speakers: A, B\n" + "".join(f"{'AB'[i % 2]}: line {i}\n" for i in range(50)),
                anchor=datetime(2026, 5, 1).date())
    times = [datetime.fromisoformat(m["time"]) for m in res.messages]
    assert all(b > a for a, b in zip(times, times[1:]))
    assert times[0].isoformat() == "2026-05-01T12:00:00-04:00"


def test_a_full_timestamp_dates_the_undated_lines_after_it():
    res = parse("# speakers: A, B\n[2026-04-02 08:00] A: timed\nB: undated\n")
    assert [m["time_precision"] for m in res.messages] == ["minute", "day"]
    assert res.messages[1]["time"] == "2026-04-02T08:00:00.001-04:00"  # just after the row before it
    back = T.parse(T.write(res.messages, TZ), TZ, key=UNKEYED)
    assert without_lines(back.messages) == without_lines(res.messages)


def test_names_need_two_lines_without_a_speakers_header():
    res = parse("# source: paste\n"
                "Mike: hi\n"
                "Dave: hey\n"
                "Note: this line is part of Dave's message\n"
                "Mike: ok\n"
                "Dave: fine\n")
    assert [m["sender"] for m in res.messages] == ["Mike", "Dave", "Mike", "Dave"]
    assert res.messages[1]["text"] == "hey\nNote: this line is part of Dave's message"
    assert res.fmt["speakers"] == "inferred"


@pytest.mark.parametrize("line", ["12345: not a name", "https: not a name", "x" * 41 + ": too long"])
def test_what_is_not_a_name(line):
    res = parse(f"# speakers: Mike\nMike: hi\n{line}\n")
    assert len(res.messages) == 1 and res.messages[0]["text"].endswith(line.split(": ")[1])


def test_only_listed_speakers_start_messages():
    res = parse("# speakers: Mike\nMike: hi\nDave: a quoted aside\n")
    assert len(res.messages) == 1 and res.messages[0]["text"] == "hi\nDave: a quoted aside"


def test_a_lone_quote_or_a_line_before_any_message_is_unparsed():
    res = parse("# speakers: Mike\nhello there\n> Dave: quoted\n--- 2026-01-01 ---\nMike: hi\n")
    assert [n for n, _ in res.unparsed] == [2, 3] and len(res.messages) == 1


@pytest.mark.parametrize("text,code_line", [
    ("# source: two words\nMike: hi\n", 1),
    ("# speaker: Mike\nMike: hi\n", 1),
    ("# speakers: Mike, Mike\nMike: hi\n", 1),
    ("# speakers: 123\nMike: hi\n", 1),
    ("# speakers: Mike\n--- 2026-02-30 ---\nMike: hi\n", 2),
    ("# speakers: Mike\n[2026-01-01 25:00] Mike: hi\n", 2),
])
def test_invalid_transcripts_name_the_line(text, code_line):
    with pytest.raises(ChatcomError) as e:
        parse(text)
    assert e.value.code == "CHATCOM_E_TRANSCRIPT_INVALID" and e.value.context["line"] == code_line
    assert "docs/transcript-format.md" in e.value.fix


def test_ids_of_undated_rows_do_not_depend_on_where_the_import_starts():
    full = parse("# speakers: A, B\nA: one\nB: two\nA: three\nB: four\n").messages
    tail = parse("# speakers: A, B\nB: two\nA: three\nB: four\n").messages
    assert [m["id"] for m in full[2:]] == [m["id"] for m in tail[1:]]
    rep = parse("# speakers: A\nA: lol\nA: lol\nA: lol\n").messages
    assert len({m["id"] for m in rep}) == 3


# ---- write -> parse is the identity --------------------------------------------------
# Value: protects=a transcript the engine writes (screenshot review, a paste the skill saves) reads back unchanged;
# fails_when=the writer forgets an escape or the parser reads one differently; why_new=spec "Tests that must exist";
# seam=none; no_claim=names the format cannot hold (they raise ValueError)

HOSTILE = [
    "[2026-01-01 10:00] Mike: a line that looks like a message",
    "--- 2026-01-01 ---",
    "# source: paste",
    "# speakers: Eve",
    "> Mike: looks like a quote",
    ">  looks like a quote continuation",
    "\\starts with a backslash",
    "\\\\two backslashes",
    "<image>",
    "<deleted>",
    "<image> with words",
    "ends like reactions (reactions: 😂 Mike)",
    "(reactions: -)",
    "Mike: inside the text",
    "Mike:",
    "12:30 is lunch",
    "- a dash list",
    "[bracket] text",
    "👨‍👩‍👧 family emoji and 🇺🇸 flag",
    "שלום mixed עברית",
    "tab\tinside",
    "",
]


def _msg(i, sender, text, *, precision="minute", media=None, deleted=False, reactions=None, quote=None):
    return {"id": "0" * 16, "sender": sender, "time": f"2026-01-01T10:{i:02d}:00-05:00", "time_precision": precision,
            "text": text, "media": media, "deleted": deleted, "edited": False, "reply_to": None,
            "reactions": reactions, "quote": quote, "source": {"adapter": "transcript", "line": 1, "label": sender}}


def hostile_messages():
    names = ["Mike", "Dave, Jr.", "Ana\\Bo", "Speaker A", "Dave2", "Zoë", "李雷", "Unnamed 1a2b3c"]
    msgs = []
    i = 0
    for n, name in enumerate(names):
        for h in HOSTILE:
            text = "\n".join(filter(None, [h, HOSTILE[(n + i) % len(HOSTILE)], h]))
            from chatcom.core.messages import normalize_text
            msgs.append(_msg(i % 60, name, normalize_text(text)))
            i += 1
    msgs.append(_msg(1, "Mike", "", media="image"))
    msgs.append(_msg(2, "Mike", "caption\n<deleted>", media="video"))
    msgs.append(_msg(3, "Dave, Jr.", "", deleted=True, reactions=[{"emoji": "😂", "sender": None, "count": 3}]))
    msgs.append(_msg(4, "Mike", "x", reactions=[{"emoji": "👍", "sender": "Dave, Jr.", "count": 1},
                                                {"emoji": "❤️", "sender": "Ana\\Bo", "count": 2},
                                                {"emoji": "🔥", "sender": "Mike", "count": 1}]))
    msgs.append(_msg(5, "Zoë", "reply", quote={"sender": None, "text": "Mike: not a sender\n> nested"}))
    msgs.append(_msg(6, "Zoë", "reply", quote={"sender": "Dave2", "text": "\\escaped\n- dash"}))
    msgs.append(_msg(7, "Zoë", "secs", precision="exact"))
    return msgs


def test_write_then_parse_is_the_identity_over_a_hostile_corpus():
    msgs = hostile_messages()
    first = T.parse(T.write(msgs, TZ), TZ, key=UNKEYED)
    assert first.unparsed == []
    keep = ("sender", "time", "time_precision", "text", "media", "deleted", "reactions", "quote")
    for a, b in zip(msgs, first.messages, strict=True):
        assert {k: a[k] for k in keep} == {k: b[k] for k in keep}, (a, b)
    again = T.parse(T.write(first.messages, TZ), TZ, key=UNKEYED)
    assert without_lines(again.messages) == without_lines(first.messages)


def test_write_then_parse_keeps_day_and_order_rows():
    res = parse("# speakers: A, B\nA: undated\nB: undated too\n--- 2026-02-02 ---\nA: dated\n--- 2026-02-03 ---\n"
                "B: next day\n", anchor=datetime(2026, 1, 5).date())
    back = T.parse(T.write(res.messages, TZ), TZ, key=UNKEYED, anchor=datetime(2026, 1, 5).date())
    assert without_lines(back.messages) == without_lines(res.messages)


def test_the_writer_refuses_what_the_format_cannot_hold():
    for bad in ("Re: Mike", "#hash", "x" * 41, "1234"):
        with pytest.raises(ValueError):
            T.write([_msg(0, bad, "hi")], TZ)
    with pytest.raises(ValueError):
        T.write([_msg(0, "A", "x", precision="day"), _msg(1, "A", "y", precision="order")], TZ)


# ---- sniffing, exclusions, the CLI -------------------------------------------------------

def test_a_header_makes_a_transcript_and_nothing_else_does(tmp_path):
    head = tmp_path / "paste.txt"
    head.write_text("\ufeff\n# source: paste\nMike: hi\nDave: hey\n", "utf-8")
    assert registry.sniff([head]).name == "transcript"
    plain = tmp_path / "plain.txt"
    plain.write_text("Mike: hi\nDave: hey\nMike: ok\nDave: fine\n", "utf-8")
    with pytest.raises(ChatcomError) as e:
        registry.sniff([plain])
    assert e.value.code == "CHATCOM_E_SOURCE_UNKNOWN"
    assert registry.sniff([plain], "transcript").name == "transcript"


def test_exclusion_removes_names_from_reactions_and_quotes():
    res = parse("# speakers: Mike, Dave, Sam\n> Mike: hi @Sam\nDave: yo (reactions: 😂 Sam, 👍 Mike)\n"
                "Sam: secret\n")
    out, dropped = window.apply_exclusions(res.messages, ["Sam"])
    assert dropped == 1 and len(out) == 1
    assert out[0]["reactions"] == [{"emoji": "😂", "sender": None, "count": 1},
                                   {"emoji": "👍", "sender": "Mike", "count": 1}]
    assert out[0]["quote"]["text"] == "hi @[someone]"
    assert "Sam" not in json.dumps(out)


def test_a_pasted_chat_becomes_a_show_and_resumes_next_week(capsys, chatcom_home, tmp_path, not_a_tty):
    code, out, err = run(capsys, "--json", "new", str(PASTE), "--show", "lake-paste", "--operator", "Dave",
                         "--tz", TZ)
    assert code == 0, err
    info = json.loads(out)
    assert info["source"] == "transcript"
    code, out, err = run(capsys, "--json", "ingest", "lake-paste", "--export", str(PASTE), "--accept-data-flow")
    assert code == 0, err
    week2 = Path(info["inbox"]) / "paste-week2.txt"
    week2.write_text(PASTE.read_text("utf-8") + "[2026-01-16 20:00] Sam: same time next week?\n", "utf-8")
    show = chatcom_home / "lake-paste"
    st = json.loads((show / "episodes" / "S01E01" / "state.json").read_text())
    assert st["source"] == "transcript"
    code, out, err = run(capsys, "--json", "episode", "lake-paste", "--dry-run", "--export", str(week2))
    assert code == 0, err


def test_unparsed_lines_over_the_limit_fail(capsys, chatcom_home, tmp_path, not_a_tty):
    f = tmp_path / "messy.txt"
    f.write_text("# source: paste\n# speakers: Mike\n" + "stray line\n" * 5 + "Mike: hi\n", "utf-8")
    code, _, err = run(capsys, "new", str(f), "--operator", "Mike", "--tz", TZ)
    assert code == 2 and "CHATCOM_E_UNPARSED_LINES" in err and "transcript" in err


# Value: protects=a row with no time between timed rows stays between them after the window sorts; fails_when=
# undated rows are pinned to 12:00 again; why_new=PR 2 review; seam=none; no_claim=sources that go back in time
@pytest.mark.parametrize("src,order", [
    ("[2026-03-01 09:00] A: 1\nB: 2\n[10:00] C: 3\n", ["1", "2", "3"]),
    ("--- 2026-03-01 ---\nB: 1\n[10:00] C: 2\n", ["1", "2"]),
    ("--- 2026-03-01 ---\n[13:00] A: 1\nB: 2\nC: 3\n[13:01] A: 4\n", ["1", "2", "3", "4"]),
    ("A: 1\nB: 2\n[2026-03-01 08:00] C: 3\nA: 4\n", ["1", "2", "3", "4"]),
])
def test_rows_with_no_time_keep_chat_order(src, order):
    res = parse("# speakers: A, B, C\n" + src)
    assert [m["text"] for m in window.dedupe(res.messages)] == order
    times = [datetime.fromisoformat(m["time"]) for m in res.messages]
    assert all(b > a for a, b in zip(times, times[1:]))


def test_a_time_with_no_date_is_kept_in_order_without_a_clock():
    res = parse("# speakers: A, B\n[19:02] A: hi\n[19:03] B: yo\n", anchor=datetime(2026, 5, 1).date())
    assert [m["time_precision"] for m in res.messages] == ["order", "order"]


@pytest.mark.parametrize("who", ["Mom (cell)", "Bob ×2", "42", "a\\b, c", "(x)"])
def test_reaction_senders_round_trip(who):
    m = _msg(0, "A", "x", reactions=[{"emoji": "👍", "sender": who, "count": 1},
                                    {"emoji": "😂", "sender": who, "count": 3}])
    back = T.parse(T.write([m], TZ), TZ, key=UNKEYED).messages[0]
    assert back["text"] == "x" and back["reactions"] == m["reactions"]


# Value: protects=no phone number reaches the model through a name, a quote sender or a reaction; fails_when=a
# name skips mask_numbers or a long number reads as a count; why_new=PR 2 adversarial review; seam=none;
# no_claim=numbers the masking module itself does not recognise
def test_phone_numbers_inside_names_are_masked():
    res = parse("# speakers: Mike 555-010-2233, Dave\n"
                "> my cell is 555 010 2233: call me\n"
                "Dave: hi (reactions: 👍 my cell 555 010 2233, 😂 5550102233)\n"
                "Mike 555-010-2233: yo\n")
    blob = json.dumps(res.messages)
    assert "2233" not in blob and "0102233" not in blob
    q = res.messages[0]["quote"]
    assert q["sender"] is None and "Unnamed" in q["text"]  # not a speaker, so quoted text, masked
    assert all(r["sender"] and "Unnamed" in r["sender"] for r in res.messages[0]["reactions"])
    assert res.messages[1]["sender"].startswith("Mike Unnamed")


def test_a_quote_from_someone_not_speaking_round_trips():
    m = _msg(0, "A", "reply", quote={"sender": "Gone Guy", "text": "earlier"})
    back = T.parse(T.write([m], TZ), TZ, key=UNKEYED).messages[0]
    assert back["quote"] == m["quote"]
