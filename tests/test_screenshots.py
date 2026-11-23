"""Step 2.5 PR 4: screenshot intake (manifest, checks, clean copies, tiles), the
join rules (core/screens.py), the ReplayReader and the keyless end-to-end path."""
import json
import shutil
import struct
import zlib
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from chatcom import cli
from chatcom.adapters import images as I, registry
from chatcom.adapters.readers.replay import ReplayReader
from chatcom.core import screens as S, state as state_ops
from chatcom.core.errors import ChatcomError
from chatcom.core.schema import problems
from conftest import ROOT, TZ

FIX = ROOT / "tests" / "fixtures" / "screenshots"
NY = ZoneInfo(TZ)
HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


def run(capsys, *argv):
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def not_a_tty(monkeypatch):
    monkeypatch.setattr(cli, "_tty", lambda: False)


# ---- tiny image writers (no Pillow) --------------------------------------------------

def _tiff(when: str) -> bytes:
    """A little-endian TIFF block with IFD0 -> Exif IFD -> DateTimeOriginal."""
    val = when.encode() + b"\0"
    ifd0 = 8
    exif = ifd0 + 2 + 12 + 4
    data = exif + 2 + 12 + 4
    out = b"II*\0" + struct.pack("<I", ifd0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif) + struct.pack("<I", 0)
    out += struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(val), data) + struct.pack("<I", 0)
    return out + val


def _chunk(typ: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + typ + body + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF)


def png(w: int, h: int, *, shot: str | None = None, rgb=(240, 240, 240)) -> bytes:
    raw = b"".join(b"\0" + bytes(rgb) * w for _ in range(h))
    out = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    if shot:
        out += _chunk(b"eXIf", _tiff(shot))
    return out + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


def jpeg_header(w: int, h: int, *, shot: str | None = None) -> bytes:
    out = b"\xff\xd8"
    if shot:
        app1 = b"Exif\0\0" + _tiff(shot)
        out += b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    sof = struct.pack(">BHHB", 8, h, w, 3) + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    return out + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9"


# ---- intake ---------------------------------------------------------------------------------
# Value: protects=only real images of a sane size are copied, in capture order; fails_when=a non-image or a pixel
# bomb reaches the decoder, or the order ignores EXIF; why_new=new intake; seam=none; no_claim=every EXIF variant

def test_kind_and_size_come_from_the_header():
    assert I.kind_of(png(3, 2)[:16]) == "png" and I.dimensions(png(3, 2), "png") == (3, 2)
    assert I.kind_of(jpeg_header(640, 480)[:16]) == "jpeg" and I.dimensions(jpeg_header(640, 480), "jpeg") == (640, 480)
    webp = b"RIFF\0\0\0\0WEBPVP8X" + b"\0" * 8 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
    assert I.kind_of(webp[:16]) == "webp" and I.dimensions(webp, "webp") == (640, 480)
    assert I.kind_of(b"\0\0\0\x18ftypheic" + b"\0" * 4) == "heic"
    assert I.kind_of(b"GIF89a" + b"\0" * 10) is None


def test_capture_time_comes_from_exif():
    assert I.exif_time(png(2, 2, shot="2026:01:09 19:05:00"), "png") == datetime(2026, 1, 9, 19, 5)
    assert I.exif_time(jpeg_header(2, 2, shot="2026:01:10 08:00:00"), "jpeg") == datetime(2026, 1, 10, 8, 0)
    assert I.exif_time(png(2, 2), "png") is None


def test_the_manifest_orders_by_capture_time_then_name(tmp_path):
    (tmp_path / "b.png").write_bytes(png(10, 10, shot="2026:01:09 19:00:00"))
    (tmp_path / "a.png").write_bytes(png(10, 10, shot="2026:01:09 19:10:00"))
    (tmp_path / "c.png").write_bytes(png(10, 10, shot="2026:01:09 19:00:00"))
    (tmp_path / ".DS_Store").write_bytes(b"x")
    m = I.manifest([tmp_path])
    assert [e.name for e in m] == ["b.png", "c.png", "a.png"] and all(e.captured_from == "exif" for e in m)


@pytest.mark.parametrize("data,why", [(b"%PDF-1.7" + b"\0" * 20, "not a PNG"),
                                      (png(10000, 10000)[:40], "pixel limit")])
def test_bad_images_are_refused_before_decoding(tmp_path, data, why):
    (tmp_path / "x.png").write_bytes(data)
    with pytest.raises(ChatcomError) as e:
        I.manifest([tmp_path / "x.png"])
    assert e.value.code == "CHATCOM_E_IMAGE_UNREADABLE" and why in e.value.what


def test_too_many_images(tmp_path):
    for i in range(3):
        (tmp_path / f"{i}.png").write_bytes(png(4, 4))
    with pytest.raises(ChatcomError) as e:
        I.manifest([tmp_path], max_images=2)
    assert e.value.code == "CHATCOM_E_TOO_MANY_IMAGES"


def test_tall_images_split_into_overlapping_tiles():
    assert I.tiles(100, 200) == [(0, 200)]
    t = I.tiles(100, 700)
    assert t[0] == (0, 200) and t[-1][0] + t[-1][1] == 700
    assert all(b[0] < a[0] + a[1] for a, b in zip(t, t[1:]))  # each overlaps the next


@needs_ffmpeg
def test_copies_are_re_encoded_without_metadata_and_tiled(tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "short.png").write_bytes(png(20, 30, shot="2026:01:09 19:00:00"))
    (src / "tall.png").write_bytes(png(20, 120, shot="2026:01:09 19:01:00"))
    copies = I.copy_all(I.manifest([src]), tmp_path / "out")
    n = len(I.tiles(20, 120))
    assert n > 1 and [c["file"] for c in copies] == ["001.png"] + [f"002-{k}.png" for k in range(1, n + 1)]
    for c in copies:
        data = (tmp_path / "out" / c["file"]).read_bytes()
        assert I.kind_of(data[:16]) == "png" and b"eXIf" not in data
    assert I.dimensions((tmp_path / "out" / "002-1.png").read_bytes(), "png") == (20, 40)


@needs_ffmpeg
def test_an_image_that_does_not_decode_is_unreadable(tmp_path):
    bad = png(20, 20)[:33] + b"garbage" * 20
    (tmp_path / "bad.png").write_bytes(bad)
    with pytest.raises(ChatcomError) as e:
        I.copy_all(I.manifest([tmp_path / "bad.png"]), tmp_path / "out")
    assert e.value.code == "CHATCOM_E_IMAGE_UNREADABLE"


# ---- the join -----------------------------------------------------------------------------------
# Value: protects=the fixed rules that turn per-image rows into one chat; fails_when=seam rows repeat, order flips,
# names drop, dates resolve wrong; why_new=new core module; seam=none; no_claim=reader accuracy (PR 7 eval)

def row(text, side="left", name=None, time=None, **kw):
    return {"type": "message", "side": side, "name": name, "text": text, "time": time, **kw}


def img(name, rows, day=date(2026, 1, 9), chat=None):
    return S.Image(name, day, rows, chat)


def test_parse_time_and_dates():
    assert S.parse_time("7:02 PM") == (19, 2) and S.parse_time("19:02") == (19, 2) and S.parse_time("12:05 am") == (0, 5)
    assert S.parse_time("25:00") is None and S.parse_time("13:00 PM") is None
    ref = date(2026, 1, 9)  # a Friday
    assert S.resolve_date("Today", ref) == ref and S.resolve_date("Yesterday", ref) == date(2026, 1, 8)
    assert S.resolve_date("Monday", ref) == date(2026, 1, 5) and S.resolve_date("friday", ref) == ref
    assert S.resolve_date("Jan 3", ref) == date(2026, 1, 3) and S.resolve_date("Dec 30", ref) == date(2025, 12, 30)
    assert S.resolve_date("3 January 2025", ref) == date(2025, 1, 3) and S.resolve_date("2026-01-02", ref) == date(2026, 1, 2)
    assert S.resolve_date("someday", ref) is None


def test_seam_duplicates_are_dropped_and_names_carry_across():
    a = img("1", [{"type": "date", "text": "Today"}, row("who has the grill", name="Mike", time="19:02"),
                  row("not me", time="19:03"), row("me neither", side="right", time="19:04")])
    b = img("2", [row("not me", time="19:03"), row("me neither", side="right", time="19:04"),
                  row("ok I'll bring it", time="19:05")])
    j = S.join([a, b], tz=NY, operator="Dave")
    assert [(m["label"], m["text"]) for m in j.messages] == [
        ("Mike", "who has the grill"), ("Mike", "not me"), ("Dave", "me neither"), ("Mike", "ok I'll bring it")]
    assert j.seam_drops == 2 and not j.reversed
    assert all(m["time_precision"] == "minute" for m in j.messages)
    assert j.messages[0]["time"] == "2026-01-09T19:02:00-05:00"


def test_one_row_seams_need_a_partial_or_the_same_time():
    a = img("1", [row("first", name="Mike"), row("lol")])
    b = img("2", [row("lol"), row("next")])
    assert len(S.join([a, b], tz=NY, operator="Dave").messages) == 4  # a lone "lol" may be said twice
    a2 = img("1", [row("first", name="Mike"), row("a long message that got cut", partial="bottom")])
    b2 = img("2", [row("a long message that got cut off at the bottom"), row("next")])
    j = S.join([a2, b2], tz=NY, operator="Dave")
    assert [m["text"] for m in j.messages] == ["first", "a long message that got cut off at the bottom", "next"]


def test_newest_first_screenshots_are_reversed():
    a = img("1", [row("c", name="Mike"), row("d")])
    b = img("2", [row("a", name="Mike"), row("b"), row("c"), row("d")][:3] + [row("c")])
    old = img("old", [row("a", name="Mike"), row("b"), row("c")])
    new = img("new", [row("b", name="Mike"), row("c"), row("d")])
    j = S.join([new, old], tz=NY, operator="Dave")
    assert j.reversed and [m["text"] for m in j.messages] == ["a", "b", "c", "d"]
    assert not S.join([new, old], tz=NY, operator="Dave", reverse=False).reversed
    del a, b


def test_speakers_and_labels():
    j = S.join([img("1", [row("hi"), row("yo", side="right"), row("sup", name="~ Theo")])], tz=NY, operator="Dave")
    assert [m["label"] for m in j.messages] == ["Speaker A", "Dave", "Theo"]
    j = S.join([img("1", [row("hi"), row("yo", side="right")])], tz=NY, operator="Dave",
               speakers={"A": "Mike", "right": "David"})
    assert [m["label"] for m in j.messages] == ["Mike", "David"]
    with pytest.raises(ChatcomError) as e:
        S.join([img("1", [row("hi")])], tz=NY, operator="Dave", speakers={"Z": "Zed"})
    assert e.value.code == "CHATCOM_E_SPEAKER_UNMAPPED" and "Speaker A" in e.value.fix


def test_dates_set_precision_and_the_anchor_overrides():
    rows = [row("before any date", name="Mike", time="09:00"), {"type": "date", "text": "Yesterday"},
            row("dated, no time"), row("dated and timed", time="10:00")]
    j = S.join([img("1", rows)], tz=NY, operator="Dave")
    assert [m["time_precision"] for m in j.messages] == ["order", "day", "minute"]
    assert j.messages[2]["time"].startswith("2026-01-08T10:00")
    j = S.join([img("1", rows)], tz=NY, operator="Dave", anchor=date(2026, 1, 7))
    assert [m["time_precision"] for m in j.messages] == ["minute", "day", "minute"]
    # the anchor dates the rows before the first header; "Yesterday" is still the day before the capture
    assert j.messages[0]["time"].startswith("2026-01-07T09:00") and j.messages[2]["time"].startswith("2026-01-08")


def test_placeholders_reactions_and_quotes_pass_through():
    r = row("", name="Mike", placeholder="image", reactions=[{"emoji": "😂", "sender": None, "count": 2}],
            quote={"sender": "Theo", "text": "where is it"})
    m = S.join([img("1", [r, row("", placeholder="deleted")])], tz=NY, operator="Dave").messages
    assert (m[0]["media"], m[0]["reactions"][0]["count"], m[0]["quote"]["sender"]) == ("image", 2, "Theo")
    assert m[1]["deleted"] and m[1]["text"] == ""


def test_reads_are_not_changed_by_the_join():
    a = img("1", [row("x", name="Mike", partial="bottom")])
    b = img("2", [row("x y z"), row("next")])
    before = json.dumps([a.rows, b.rows])
    S.join([a, b], tz=NY, operator="Dave")
    assert json.dumps([a.rows, b.rows]) == before


# ---- the replay reader and the end-to-end path -------------------------------------------------
# Value: protects=the keyless screenshot path from images to a drafted episode, with consent first; fails_when=any
# stage is skipped or a read happens before consent; why_new=spec "Tests that must exist"; seam=ReplayReader;
# no_claim=a live reader (PR 6)

def test_replay_reads_validate_against_the_schema():
    doc = json.loads((FIX / "lake-reads.json").read_text("utf-8"))
    assert all(problems("read", r) == [] for r in doc["reads"].values())
    with pytest.raises(ChatcomError):
        ReplayReader(ROOT / "pyproject.toml")


def _shots(tmp_path) -> Path:
    d = tmp_path / "shots"
    d.mkdir()
    for i, name in enumerate(["IMG_0001.PNG", "IMG_0002.PNG", "IMG_0003.PNG"]):
        # different pixels: the read cache is keyed by the copy's content
        (d / name).write_bytes(png(30, 60, shot=f"2026:01:09 19:{10 + i:02d}:00", rgb=(200 + i, 240, 240)))
    return d


@needs_ffmpeg
def test_screenshots_become_a_show_only_after_consent(capsys, chatcom_home, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    reads = str(FIX / "lake-reads.json")
    code, out, err = run(capsys, "--json", "new", str(shots), "--show", "lake-shots", "--operator", "Dave",
                         "--tz", TZ, "--writer", "replay", "--replay-reads", reads)
    assert code == 6 and "nothing leaves this computer" in json.loads(out)["notice_text"]
    assert not (chatcom_home / "cache").exists() and not (chatcom_home / "lake-shots").exists()  # nothing read
    code, out, err = run(capsys, "--json", "new", str(shots), "--show", "lake-shots", "--operator", "Dave",
                         "--tz", TZ, "--writer", "replay", "--replay-reads", reads, "--accept-data-flow")
    assert code == 0, err
    info = json.loads(out)
    assert info["source"] == "screenshots" and set(info["cast"]) >= {"Dave", "Mike"}
    imports = list((chatcom_home / "lake-shots" / "imports").glob("*/transcript.txt"))
    assert len(imports) == 1 and imports[0].read_text("utf-8").startswith("# source: screenshots")


@needs_ffmpeg
def test_review_stops_at_read_and_the_transcript_continues(capsys, chatcom_home, tmp_path, not_a_tty,
                                                           beats_s01e01):
    shots = _shots(tmp_path)
    reads = str(FIX / "lake-reads.json")
    assert run(capsys, "new", str(shots), "--show", "lake-house", "--operator", "Dave", "--tz", TZ,
               "--cast", "Dave", "--cast", "Mike", "--cast", "Theo", "--cast", "Priya", "--cast", "Sam",
               "--writer", "replay", "--replay-reads", reads, "--accept-data-flow")[0] == 0
    replay = tmp_path / "beats.json"
    replay.write_text(json.dumps(beats_s01e01), "utf-8")
    code, out, err = run(capsys, "--json", "episode", "lake-house", "--export", str(shots), "--writer", "replay",
                         "--replay", str(replay), "--replay-reads", reads, "--review")
    assert code == 0, err
    rev = json.loads(out)
    assert rev["stage"] == "read" and Path(rev["transcript"]).is_file() and "--export" in rev["next_command"]
    code, _, err = run(capsys, "--json", "episode", "lake-house", "--export", str(shots), "--writer", "replay",
                       "--replay", str(replay), "--replay-reads", reads)
    assert code == 2 and json.loads(err)["code"] == "CHATCOM_E_IMPORT_IN_PROGRESS"
    st = json.loads(run(capsys, "--json", "status", "lake-house")[1])
    assert st["stage"] == "read" and rev["transcript"] in st["next"]
    code, out, err = run(capsys, "--json", "episode", "lake-house", "--export", rev["transcript"], "--writer", "replay",
                         "--replay", str(replay))
    assert code == 0, err
    assert json.loads(out)["stage"] == "drafted" and json.loads(out)["source"] == "screenshots"
    msgs = (chatcom_home / "lake-house" / "episodes" / "S01E01" / "messages.jsonl").read_text("utf-8")
    assert "010-4477" not in msgs and "Unnamed" in msgs  # masked after reading, before the writer


@needs_ffmpeg
def test_replace_starts_an_import_over(capsys, chatcom_home, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    reads = str(FIX / "lake-reads.json")
    assert run(capsys, "new", str(shots), "--show", "lake-house", "--operator", "Dave", "--tz", TZ,
               "--writer", "replay", "--replay-reads", reads, "--accept-data-flow")[0] == 0
    unused = tmp_path / "unused.json"
    unused.write_text("{}", "utf-8")
    common = ["episode", "lake-house", "--export", str(shots), "--writer", "replay", "--replay", str(unused),
              "--replay-reads", reads, "--review"]
    assert run(capsys, *common)[0] == 0
    assert run(capsys, *common)[0] == 2
    assert run(capsys, *common, "--replace")[0] == 0


def test_a_dry_run_lists_screenshots_and_never_reads_them(capsys, lake_show, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    inbox = lake_show.inbox / "shots"
    shutil.copytree(shots, inbox)
    code, out, err = run(capsys, "--json", "episode", "lake-house", "--dry-run", "--export", str(inbox))
    assert code == 0, err
    info = json.loads(out)
    assert [i["name"] for i in info["images"]] == ["IMG_0001.PNG", "IMG_0002.PNG", "IMG_0003.PNG"]


def test_cli_mode_cannot_read_yet_and_refuses_before_any_notice(capsys, lake_show, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    inbox = lake_show.inbox / "shots"
    shutil.copytree(shots, inbox)
    code, _, err = run(capsys, "--json", "episode", "lake-house", "--export", str(inbox), "--accept-data-flow")
    assert code == 2 and "CLI mode" in json.loads(err)["what"]


# ---- the stage table (deferred from PR 1, D2) -------------------------------------------------------
# Value: protects=random command sequences never reach an undefined stage and never write the bible before
# committed; fails_when=a transition skips a stage or a command writes bible.json; why_new=spec "Tests that must
# exist"; seam=ReplayWriter; no_claim=casting and render stages (not built)

def test_random_command_sequences_stay_in_the_stage_table(capsys, lake_show, beats_s01e01, tmp_path, not_a_tty):
    import random
    from conftest import LAKE
    good = tmp_path / "good.json"
    good.write_text(json.dumps(beats_s01e01), "utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"title": "x"}), "utf-8")
    bible_before = lake_show.bible_path.read_bytes()
    commands = [
        ["ingest", "lake-house", "--export", str(LAKE)],
        ["ingest", "lake-house", "--export", str(LAKE), "--accept-data-flow"],
        ["write-prep", "lake-house"],
        ["write-submit", "lake-house", "--file", str(good), "--model", "m"],
        ["write-submit", "lake-house", "--file", str(bad), "--model", "m"],
        ["status", "lake-house"],
        ["episode", "lake-house", "--export", str(LAKE), "--writer", "replay", "--replay", str(good)],
    ]
    rng = random.Random(20261123)
    for _ in range(150):
        code, _, _ = run(capsys, "--json", *rng.choice(commands))
        assert code in (0, 2, 4, 6)
        sp = lake_show.state_path("S01E01")
        if sp.exists():
            assert json.loads(sp.read_text("utf-8"))["stage"] in state_ops.STAGES
        assert lake_show.bible_path.read_bytes() == bible_before


@needs_ffmpeg
def test_the_joined_fixture_drops_every_seam_duplicate(capsys, chatcom_home, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    assert run(capsys, "new", str(shots), "--show", "lake-shots", "--operator", "Dave", "--tz", TZ, "--writer",
               "replay", "--replay-reads", str(FIX / "lake-reads.json"), "--accept-data-flow")[0] == 0
    t = next((chatcom_home / "lake-shots" / "imports").glob("*/transcript.txt")).read_text("utf-8")
    body = [ln for ln in t.splitlines() if not ln.startswith(("#", ">"))]
    assert len(body) == len(set(body)) == 11, t
    assert "Priya: I can bring the cooler" in t


# ---- PR 4 review fixes ----------------------------------------------------------------------------
# Value: protects=a finished episode is never reset by a screenshot import; the notice tells the truth about
# exclusion; a failed `new` leaves no copies; tiles count against the limit; fails_when=any guard is removed;
# why_new=PR 4 review; seam=ReplayReader/ReplayWriter; no_claim=HEIC (needs macOS sips)

@needs_ffmpeg
def test_review_refuses_an_episode_past_ingested(capsys, chatcom_home, tmp_path, not_a_tty, beats_s01e01):
    shots = _shots(tmp_path)
    reads = str(FIX / "lake-reads.json")
    assert run(capsys, "new", str(shots), "--show", "lake-house", "--operator", "Dave", "--tz", TZ,
               "--cast", "Dave", "--cast", "Mike", "--cast", "Theo", "--cast", "Priya", "--cast", "Sam",
               "--writer", "replay", "--replay-reads", reads, "--accept-data-flow")[0] == 0
    replay = tmp_path / "beats.json"
    replay.write_text(json.dumps(beats_s01e01), "utf-8")
    base = ["episode", "lake-house", "--export", str(shots), "--writer", "replay", "--replay", str(replay),
            "--replay-reads", reads]
    assert run(capsys, *base)[0] == 0
    sp = chatcom_home / "lake-house" / "episodes" / "S01E01" / "state.json"
    before = sp.read_text("utf-8")
    code, _, err = run(capsys, "--json", *base, "--review")
    assert code == 2 and json.loads(err)["code"] == "CHATCOM_E_STATE"
    assert sp.read_text("utf-8") == before


def test_the_screenshot_notice_does_not_claim_exclusion_before_reading():
    from chatcom import app
    t = app.notice("anthropic", ["Sam"], images=True)
    assert "before any model call" not in t and "Removed after reading, before the writer: Sam" in t
    assert "Excluded before any model call: Sam" in app.notice("anthropic", ["Sam"])


@needs_ffmpeg
def test_a_failed_new_leaves_no_copies(capsys, chatcom_home, tmp_path, not_a_tty):
    shots = _shots(tmp_path)
    code, _, err = run(capsys, "new", str(shots), "--show", "lake-shots", "--operator", "Dave", "--exclude", "Ghost",
                       "--tz", TZ, "--writer", "replay", "--replay-reads", str(FIX / "lake-reads.json"),
                       "--accept-data-flow")
    assert code == 2 and "CHATCOM_E_UNKNOWN_MEMBER" in err  # fails after the screenshots were read
    assert not (chatcom_home / "cache").exists()  # replay reads are never cached
    assert not any((chatcom_home / "imports").glob("*"))


@needs_ffmpeg
def test_tiles_count_against_the_limit(tmp_path):
    (tmp_path / "a.png").write_bytes(png(10, 100))
    with pytest.raises(ChatcomError) as e:
        I.copy_all(I.manifest([tmp_path]), tmp_path / "out", max_images=2)
    assert e.value.code == "CHATCOM_E_TOO_MANY_IMAGES"



@pytest.mark.parametrize("name", ["Signal", "Tel", "www", "x" * 39 + ":", "Mike: Jr", "line\nbreak"])
def test_any_display_name_survives_the_transcript(name):
    from chatcom.adapters import transcript as T
    j = S.join([img("1", [row("hi", name=name), row("yo", side="right")])], tz=NY, operator="Dave")
    msgs = [{"sender": m["label"], "time": m["time"], "time_precision": m["time_precision"], "text": m["text"],
             "media": None, "deleted": False, "reactions": None, "quote": None, "source": {"label": m["label"]}}
            for m in j.messages]
    back = T.parse(T.write(msgs, TZ, source="screenshots"), TZ)
    assert [m["sender"] for m in back.messages] == [m["label"] for m in j.messages]


def test_a_speaker_value_that_is_not_a_name_is_a_usage_error():
    with pytest.raises(ChatcomError) as e:
        S.join([img("1", [row("hi")])], tz=NY, operator="Dave", speakers={"A": "12345"})
    assert e.value.code == "CHATCOM_E_USAGE"
    j = S.join([img("1", [row("hi")])], tz=NY, operator="Dave", speakers={"A": "Mike: Jr"})
    assert j.messages[0]["label"] == "Mike Jr"


@needs_ffmpeg
def test_replace_clears_old_copies(tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    for k in range(3):
        (src / f"{k}.png").write_bytes(png(10, 10, rgb=(k, 0, 0)))
    out = tmp_path / "out"
    I.copy_all(I.manifest([src]), out)
    (src / "2.png").unlink()
    I.copy_all(I.manifest([src]), out)
    assert sorted(p.name for p in out.iterdir()) == ["001.png", "002.png"]


def test_review_next_command_names_the_replay_file():
    st = {"stage": "read", "transcript": "/x/transcript.txt", "writer_mode": "replay"}
    assert "--replay <file>" in state_ops.next_command(st, "s")



def test_unnamed_speakers_fail_closed_when_the_show_excludes_someone():
    from chatcom import app
    from chatcom.adapters.source import ParseResult
    res = ParseResult([{"sender": "Speaker A"}, {"sender": "Dave"}], 0, [], 2, {}, None, {}, "screenshots")
    with pytest.raises(ChatcomError) as e:
        app.check_screenshot_exclusions({"exclude": ["Sam"]}, res)
    assert e.value.code == "CHATCOM_E_SPEAKER_UNMAPPED" and "--speaker" in e.value.fix
    app.check_screenshot_exclusions({"exclude": []}, res)  # no exclusions: unnamed is fine



# ---- PR 5: the join rules tracked from PR 4 review ------------------------------------------------
# Value: protects=names never cross a gap; times never silently run backwards; fails_when=either rule is removed;
# why_new=TODOS P1 from PR 4 review; seam=none; no_claim=reader accuracy

def test_a_name_does_not_carry_across_a_gap():
    a = img("1", [row("one", name="Mike"), row("two")])
    b = img("2", [row("three"), row("four", name="Theo")])  # no overlap with image 1
    j = S.join([a, b], tz=NY, operator="Dave", reverse=False)
    assert [m["label"] for m in j.messages] == ["Mike", "Mike", "Speaker A", "Theo"]


def test_times_that_run_backwards_stop_the_join():
    rows = [{"type": "date", "text": "Today"}, row("a", name="Mike", time="19:30"), row("b", time="19:00")]
    with pytest.raises(ChatcomError) as e:
        S.join([img("1", rows)], tz=NY, operator="Dave")
    assert "out of order" in e.value.what and "--reverse" in e.value.fix
    ok = [{"type": "date", "text": "Today"}, row("a", name="Mike", time="19:30"), row("b", time="19:27")]
    assert len(S.join([img("1", ok)], tz=NY, operator="Dave").messages) == 2  # a 3-minute slip is noise


def test_past_midnight_without_a_header_is_the_next_day():
    rows = [{"type": "date", "text": "Today"}, row("late", name="Mike", time="23:50"), row("later", time="00:10")]
    m = S.join([img("1", rows)], tz=NY, operator="Dave").messages
    assert m[0]["time"].startswith("2026-01-09T23:50") and m[1]["time"].startswith("2026-01-10T00:10")



def test_a_header_between_rows_blocks_the_midnight_roll():
    rows = [{"type": "date", "text": "Today"}, row("a", name="Mike", time="23:50"), {"type": "date", "text": "Today"},
            row("b", time="00:10")]
    with pytest.raises(ChatcomError) as e:
        S.join([img("1", rows)], tz=NY, operator="Dave")
    assert "out of order" in e.value.what


def test_strip_png_keeps_only_the_pixels(tmp_path):
    f = tmp_path / "x.png"
    data = png(4, 4, shot="2026:01:09 19:00:00")
    # add a text chunk (a common place for device names) before IEND
    data = data[:-12] + _chunk(b"tEXt", b"Software\x00iPhone 99") + data[-12:]
    f.write_bytes(data)
    assert b"eXIf" in f.read_bytes() and b"tEXt" in f.read_bytes()
    I.strip_png(f)
    out = f.read_bytes()
    assert b"eXIf" not in out and b"tEXt" not in out and I.dimensions(out, "png") == (4, 4) and out.endswith(b"IEND\xaeB`\x82")
